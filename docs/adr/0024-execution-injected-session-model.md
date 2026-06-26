# Execution: the scope owns one transaction; every Capability records into it

ADR-0023 fixed the *authoring* contract and deferred the *execution* machinery. This decides it: an authored `@operation` runs inside a single framework-owned transaction that the **Execution Scope** opens and commits; Capabilities (the injected `db` session and, later, mailer/payments) are constructed per execution by registered **Providers** *against that transaction*, so the operation's effects, the observed **Effect** records, and the completion record commit as one atomic fact. This reverses tomtar's ADR-0018, pins the transaction boundary against the existing claim/lease/heartbeat loop, and fixes the shapes of effect recording, the outbox, and the Compile trigger — building only what v1's wedge (`db` capability + "succeeded but produced zero effects") needs and naming the rest as seams.

## The reversal of ADR-0018 (the ground shifted)

Tomtar's ADR-0018 considered and **rejected** "Design A: inject a framework-owned session into every Task," choosing instead to **instrument the user's own engine + attribute statements via a contextvar**. Its reasons were sound *for tomtar*: tomtar was a **drop-in runner for existing apps**, where forcing a `session` parameter into every Task body is an adoption-killing rewrite, and an un-injected session is a silent footgun.

Thrum is a different product: a **framework you author against**, where injecting `db` is not a cost but the entire thesis ("the operation can only use the objects the framework hands it" — VISION §Capabilities). The objection didn't disappear; the product changed such that it no longer applies. So thrum adopts **Design A**, and the injected, framework-constructed session becomes the **single instrumentation point** for effects.

Consequences of the reversal:
- **No contextvar attribution.** Design B's contextvar existed only because the framework didn't own the session and had to attribute statements after the fact. With injection, effects are attributable *by construction* — the session is bound to one execution.
- **The ADR-0018 cross-executor contextvar seam dissolves.** Preserving a contextvar across the thread/process-pool boundary is moot when the capability is passed as an argument, not read ambiently. (Sync/process-pool injection remains future work for an orthogonal reason — see Scope.)

## The Execution Scope owns the transaction (the `db` Capability is not special)

The transaction does **not** belong to the `db` Provider; it belongs to the **Execution Scope**. The scope opens one transaction ("Txn 2"), hands every Provider a context exposing it, and each Provider constructs a Capability that *records its effects into that transaction*. The scope — not any Provider — commits.

- **`db` Capability** = the scope's session, surfaced directly. The most *direct* capability (and the only one that reads *back* from the transaction, not just writes into it).
- **External-effect Capabilities** (mailer/payments) = recording proxies whose calls **stage rows into the same transaction** (the Outbox), dispatched post-commit.

This dissolves an earlier framing in which the `db` Provider *was* the transaction — an asymmetry that would force us to explain one exception to the Provider model on day one. Under "the scope owns the transaction," the `db` Provider yielding the session is the *simplest instance* of the single rule every Provider follows: *given the execution transaction, construct your effect-bearing object.* Nothing about `db` is exceptional.

Providers register on the **App** (`app.provide(Session, db_provider)`) — registering a Provider for `T` is exactly what makes `T` a registered capability type, which is what **Compile** resolves keyword-only params against (closing ADR-0023's named seam). A Provider is an **async context manager**; the scope enters all needed Providers on a shared **`AsyncExitStack`** and unwinds it (success or failure) for deterministic resource teardown, independent of the transaction's commit/rollback.

**v1 ships exactly one real Provider (`db`).** Mailer/payments/LLM are real Providers later; v1 proves the machinery with the one capability the wedge needs, and the registry is general so they slot in without redesign.

## Transaction boundary: two worker-side transactions, not one

The VISION's "a job runs inside one transaction" is a statement about **effects ⊕ outcome**, *not* about the claim. Claim cannot fold in — it must commit early to release the `SELECT … FOR UPDATE SKIP LOCKED` lock, which is the whole reason the lease/heartbeat machinery (ADR-0013) exists.

- **Txn 1 — Claim** (unchanged). Insert the Attempt with its lease, flip the Run → `running`, commit, release the lock. Lease + heartbeat cover liveness from here.
- **Txn 2 — Execute + Effects + Record** (the Execution Scope). Construct Capabilities against it, run the operation body, record Effects + the completion record, commit together.

**Accepted cost:** a long operation now holds an open transaction + connection for its full duration — the very thing ADR-0013's claim/execute split avoided. This is the unavoidable price of "effects commit with the outcome" (you cannot keep the effects' transaction closed until the outcome is known). Accepted for now; revisit if long operations bite (e.g. an opt-in degraded mode for long runs).

### Success and failure are asymmetric transaction shapes (deliberate)

- **Success: one transaction.** Effects ⊕ Effect-records ⊕ Attempt-close(`succeeded`) ⊕ `output` commit together. The atomicity win.
- **Failure: two transactions.** The operation raised ⇒ Txn 2 **rolls back** (partial effects *and* their Effect-records discarded — nothing landed). Then a **separate short transaction** writes Attempt-close(`failed`) + retry state (`pending` + `next_attempt_at`) — structurally today's `record.py`, which survives on the failure path. The failure outcome **must not** ride Txn 2, or it would roll back with the doomed effects and the Run would hang `running` forever.

This is required, not a compromise: a failure's whole purpose is that its effects do not persist, so there is nothing for the outcome to be atomic *with*.

## Effect recording and the Zero-Effect wedge

Effects are observed by instrumenting the framework-constructed session and are recorded **in Txn 2** (so the records that land commit atomically with the effects — no dual write). The recording mechanism is **best-effort and isolated — strengthened, not relaxed**, by sharing the operation's transaction: a listener that raised could roll back real effects, so it must never abort Txn 2. (This corrects the grill's initial "promoted to load-bearing" framing: what is load-bearing is the *atomicity of the records that do land*, not the recording's reliability.)

**v1 Effect shape:** one row per `(table, kind)` (`insert`/`update`/`delete`) per **Attempt**, with a `row_count` — table-level aggregation, not per-row primary keys (a 10k-row batch must not write 10k Effect rows). Keys off the **Attempt**: each Attempt commits its own Txn 2, so a failed Attempt's writes (and Effect-records) roll back and commit zero Effects.

**Zero-Effect** (`succeeded` but committed zero Effects) is a **derived flag**, never a `status` change — observability must not mutate lifecycle state. A legitimate no-op (idempotent re-run, a filter that correctly matched nothing) is a succeeded-with-zero-Effects Run; the flag informs rather than fails. Opt-in *enforcement* (an operation that declares it **must** write) is a later **postcondition** feature.

## Caller identity and attenuation (hook in v1, logic later)

Authority is **construction-time, Provider-expressed, and a function of the Caller** (VISION: "authorization happens when capabilities are constructed"). The Execution Scope carries a **Caller**; a Provider reads it and attenuates (read-only `db`, tenant-scoped `db`, amount-capped payments). The operation body is identical across callers; only the injected object's authority differs.

A Caller is born at the **transport boundary**. Synchronous transports (HTTP/MCP, later) supply it live. The **queue is the exception**: invocation (Enqueue, app process) and execution (Worker, later) are separated in time and process, so the Caller is **frozen onto the Run at Enqueue and thawed into the Scope at execution** — what VISION calls "restoring caller identity."

**v1** captures a **system/default** Caller through the real freeze/thaw path (Run carries a caller; scope restores it; Provider receives it) but builds **no attenuation logic** — the cheap-now/expensive-to-retrofit seam, so a real `Caller` flows the same path the moment auth and a second transport exist.

## Outbox and step-recording (seams, not v1 builds)

v1 ships only `db`, so there are no external calls to stage or memoize. Both mechanisms are **shapes fixed, build deferred** to when the first external-effect Capability lands (VISION M4):

- **Outbox** — fire-and-forget intent (email/webhook) **staged into Txn 2** (atomic with effects), drained post-commit by an **at-least-once** dispatcher (so staged effects must carry an idempotency key for the receiver).
- **Step-recording** — a *distinct* mechanism: memoize an external call's **result** by step id and replay it on retry (read-back, exactly-once memoization; the replay-engine seed).

They are deliberately **separate** (not one table): write-only-at-least-once delivery vs read-back-exactly-once memoization are opposite semantics; fusing them forces mode-specific nullable columns and forked handling everywhere. No `job_outbox`/`job_steps` table in v1 — a later additive migration is cheap pre-release (ADR-0004).

## Compile trigger and worker wiring

ADR-0023 left "what populates the capability registry and what triggers Compile" to here. **Providers populate it** (above). **Compile triggers at worker boot — after user-code import, before the first claim — at the slot `_assert_schedules` occupies today** (`worker/__init__.py`), subsuming that schedule-assertion fail-fast (the generalization ADR-0023 anticipated). Explicit `app.compile()` (tests/tooling) is the same idempotent path; worker boot is the guaranteed backstop. The claim loop must not start until Compile succeeds — you cannot construct capabilities for an operation that failed to resolve.

This forces a **wiring decision**: the Worker must be **handed the App** (which owns both the operation registry and the Provider registry) instead of reaching the implicit process-global `Registry._global`. `thrum worker` points at the user's app module; importing it populates the App; the Worker holds it, calls `app.compile()` at boot, and the Execution Scope reads the validated registry from it.

## Scope (built vs named-and-deferred in v1)

- **Built:** the Execution Scope + one transaction; the Provider/`AsyncExitStack` mechanism with the single `db` Provider; in-session Effect recording (table-level, per-Attempt) + the Zero-Effect flag; the success/failure transaction split; Caller freeze/thaw with a system caller; Compile at worker boot + App-as-entrypoint wiring.
- **Named, not built:** external-effect Providers; attenuation logic; the Outbox dispatcher and `job_outbox`; step-recording and `job_steps`; **sync (`def`, thread-pool) and `cpu_bound` (process-pool) injected execution** — genuinely harder, not a trivial extension: an async session can't be used from a `run_in_executor` thread as-is, and a process pool can't receive a live session/transaction across the process boundary at all. v1 injected-session execution is **async-only** (inheriting ADR-0005's slice boundary).

## Considered options

- **Keep Design B (instrument the user's own engine + contextvar attribution).** Rejected: it was right for tomtar's drop-in-runner product, wrong for thrum's author-against-the-framework product, and it forfeits the capability thesis (the framework must *own* the object to attenuate and bind it). See the reversal section.
- **The `db` Provider owns the transaction.** Rejected: forces one exception to the Provider model on day one. "The scope owns the transaction, every Capability records into it" makes `db` the simplest case of one uniform rule instead.
- **A single transaction including Claim.** Impossible: Claim must commit to release the SKIP LOCKED lock; the lease/heartbeat split (ADR-0013) exists precisely so execution happens outside the claim transaction.
- **Record the failure outcome inside Txn 2.** Rejected: it rolls back with the discarded effects and the Run hangs `running`. Failure is two transactions by necessity.
- **Auto-fail "succeeded but zero effects."** Rejected: observability must not change `status`; legitimate no-ops exist; auto-failing collides with at-least-once retry (ADR-0014). It is a derived flag; enforcement is opt-in postconditions later.
- **One table for outbox + step-recording.** Rejected: opposite semantics (at-least-once write-only vs exactly-once read-back) force mode-specific nullable columns and forked handling.

## Consequences

- `worker/execute.py`'s pure, out-of-transaction execution and `run_once`'s three-transaction (claim | execute | record) shape are replaced on the **success** path by claim | (execute ⊕ effects ⊕ record); `record.py` survives as the **failure**-path second transaction. The PORT SEAM notes in both files are now spent.
- New schema: an `effects` table (per-Attempt, table-level). No outbox/steps tables yet.
- The Worker gains an App handle and a Compile call at boot; `Registry._global` reach is retired in favor of App-owned registries.
- This is an execution-path + schema commitment and expensive to reverse — hence recorded. CONTEXT.md carries the resulting vocabulary (Effect, Zero-Effect, Provider, Execution Scope, Caller; Capability's construction seam resolved).
