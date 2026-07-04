# Worker execution model: three contexts, claim-to-capacity, pluggable executor

> **Amended (v1 0006 grilling).** Two claims below describe end-state intent the v1 code does not yet realize; calling it out so slices aren't written against phantom guarantees:
> 1. **Claim-to-capacity / concurrency** ("up to `(max − in_flight)` rows per poll… natural backpressure") is **not built in v1** — `run_once` dispatches the claimed batch *serially*. Slice 0006 routes sync→threads to free the event loop but keeps serial dispatch; realizing true concurrent in-flight execution is a **v2 engine-hardening completion** and a prerequisite for v2 Queue controls (see both roadmaps).
> 2. **"They get the async SQLAlchemy session"** overstates it: the Worker injects **no** session (the execution path passes inputs only), and **ADR-0018 rejected session injection** (Design A) in favour of instrument-the-engine + contextvar attribution (Design B). Read this line as "async Tasks run on the loop where an async session is *available*," not as a Worker-injected session. The "session is the sensor" promise is honoured in **v2 lineage**, not v1.

> **Amended (2026-07-03, verified against `main`).** Beyond the two caveats above, the code has diverged far enough from this ADR that it should be read as intent, not description. What is actually false today:
> 1. **The three-context executor does not exist.** There is no thread pool and no process pool. `worker/scope.py::_invoke` calls the Operation body directly and awaits it only if the *return value* is awaitable — so a **sync `def` Task runs inline on the event loop and blocks it**, the very thing threads were meant to prevent. Nothing routes on `cpu_bound`.
> 2. **`cpu_bound` is dead metadata.** It is a real field on `Operation` and a real `@operation` kwarg, but it is only stored — no execution path ever reads it.
> 3. **Routing is not "auto-detected via `asyncio.iscoroutinefunction`."** That call is never made; dispatch inspects the return value with `inspect.isawaitable`, and sync functions get no special handling at all.
> 4. **There is no pluggable routing policy table** (Consequences #1) — pluggable or otherwise. Routing simply isn't implemented.
> 5. **Claim-to-capacity subtraction is not built** (also flagged above): `claim_runs` claims up to `max_in_flight` flat, with no `in_flight` counter and no subtraction. `SELECT … FOR UPDATE SKIP LOCKED` and the configured `max_in_flight` cap *are* real.
> 6. **Runtime-observation lineage** (Consequences #3) is unbuilt. `EffectRecorder.observing` records **Effects** (ADR-0024), not tables-touched lineage.
>
> Note also that caveat 2 above is itself now stale: ADR-0024 reversed the no-session-injection stance, and the execution path does inject a session into Providers.

A single Worker is one asyncio event loop. Tasks route to one of three execution contexts:

- **`async def` Tasks → run as coroutines on the loop** (auto-detected via `asyncio.iscoroutinefunction`). They get the async SQLAlchemy session. The happy path.
- **sync `def` Tasks → run in a thread pool** (auto-detected). Running them on the loop would block every other in-flight Run; threads suit sync I/O-bound work (GIL releases on I/O).
- **CPU-bound Tasks → run in a process pool**, opted into explicitly (`cpu_bound=True`). True CPU parallelism needs separate processes; CPU-heaviness can't be reliably auto-detected, and defaulting sync work to processes would pay serialization cost on every sync I/O job.

**Concurrency / backpressure:** a Worker has a configured max in-flight Run count and claims via `SELECT … FOR UPDATE SKIP LOCKED` only up to `(max − in_flight)` rows per poll. Concurrency is therefore the natural backpressure — a saturated Worker claims nothing and leaves Runs for other Workers. No separate rate-limiting machinery in v1.

## Consequences

- **The executor is a pluggable routing policy, not a hardcoded mapping.** Python's free-threaded (no-GIL) future is opt-in and immature in mid-2026, so the three-context model holds for v1 — but when free-threading matures, a thread pool gains true CPU parallelism, collapsing the thread-vs-process split. Structuring routing as a policy table means flipping `cpu_bound → thread pool` later is a config change, not a redesign, and it would promote CPU-bound Tasks back to first-class for the session-sensor features.
- **Transactional enqueue is unaffected by the execution context** — it happens at *creation* time in the app process, not at execution. The process pool has no bearing on it.
- **Lineage uses sampled runtime observation, not static analysis and not per-execution.** Tables-touched can't be reliably determined statically (dynamic queries, raw SQL); it's observed through an instrumented session on a *sample* of executions (first run / 1-in-N / until stable), cached, and re-observed on version change. The only sensor gap is a Task that *both* runs in the process pool *and* touches the ORM — a small, shrinking intersection.
