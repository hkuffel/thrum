# The Operation absorbs the Task: one authored primitive, projected onto the queue

Thrum carried **two unreconciled primitives**: the framework-wide `@operation` (a stub) and the ported job surface `Task` / `@registry.task` / `enqueue` (which actually works). The VISION wants **one** primitive — "a job is an operation projected onto a queue." We decided the **Operation is the sole authored unit**; `@registry.task` is **absorbed** and ceases to be an authoring surface. The developer writes one `@operation` and reaches the queue through a projection (`op.enqueue(...)`). This ADR fixes the *authoring contract*; the *execution* machinery (capability construction/injection, single-transaction commit, effect recording, outbox) is named-but-deferred to a later decision.

This is the keystone decision for the framework, so the reasoning is recorded here while [CONTEXT.md](../CONTEXT.md) holds the resulting vocabulary (Operation, App, Capability, Data, Compile; Task retired).

## What an Operation is

A normal Python function with typed inputs and declared capabilities, projected onto transports (queue, HTTP, MCP, CLI). The signature *is* the contract:

```python
@operation(timeout=30, cpu_bound=False, retries=3)
async def send_receipts(customer_id: CustomerId, *, db: Session) -> Receipt:
    ...

send_receipts.schedule("0 9 * * *", tz="America/Vancouver")   # co-located trigger, 0..N
send_receipts.enqueue(session, customer_id=cid)               # queue projection
```

- **Identity** is `namespace.name`, inherited from the `Registry` the Operation attaches to (`@billing.operation` → `billing.send_receipts`; a bare `@operation` → `default.…`). User-owned, stable across refactors, **never** derived from import path, fail-fast on collision. This contract moves *verbatim* from the old Task onto the Operation — it is about how a `Run` row points back to code, which is independent of what the unit is called.
- **`Registry` and `App` are distinct objects.** Registry owns *identity* (it declares the namespace Operations are authored under). App owns *transports* (`app.http.post(...)`, `app.tools.expose(...)`, `app.cli.command(...)`). One does naming, the other does reaching.

## The data / capability split

A parameter is a **Capability** iff it is **keyword-only AND its annotated type is a registered capability type**. Both conditions are necessary; keyword-only alone is not sufficient. Everything else is **Data**: positional params are required data; keyword-only params of a non-capability type are optional data (e.g. `since: date | None = None`). This is what lets one signature carry required data, optional data, and injected capabilities without ceremony — which is required, because a projection must serialize *all* the data and inject *only* the capabilities.

The literal VISION phrasing ("keyword-only parameters are capabilities") was sharpened here precisely because keyword-only is also the idiomatic way to write optional *data* arguments; taking it literally would force the framework to try to construct `since`/`dry_run` as capabilities.

## Config is cut three ways, not "operation vs queue"

An earlier framing put `retry` + `timeout` + `cpu_bound` all on "the queue projection." On inspection that cut is wrong, and the corrected cut keeps config **co-located with the Operation** (the developer's stated requirement) without a second decorator:

- **Execution nature** (`timeout`, `cpu_bound`) — intrinsic to the Operation (how/how-long it runs, transport-independent). Rides on `@operation(...)`.
- **Durability policy** (`retries`, backoff) — an Operation-level *default* that durable projections (queue/timer) read and others (HTTP) ignore; overridable per-enqueue (`op.enqueue(..., retries=5)`). Also on `@operation(...)`.
- **Triggers** (`Schedule`) — genuinely separate (0..N per Operation), declared as a co-located `op.schedule(cron, tz=...)` statement under the def, **not** a stacked decorator (a schedule does not transform the function; a decorator would imply it does, and stacking breaks down at 2+ schedules).

So there is exactly **one decorator** and nothing is configured far from the Operation — the middle ground between decorator-soup and Temporal-style caller-specified policy.

## The queue projection and the session seam

`op.enqueue(session, **inputs)`: `session` is the caller's transactional handle (`.enqueue`'s own first argument), and `**inputs` are the Operation's Data. The caller's session stays **explicit**, not pulled from a contextvar, because the entire value of transactional enqueue (ADR-0001/0006) is that the Run joins *a specific transaction* — guessing it reintroduces the dual-write ambiguity the design exists to kill.

**Named seam (mechanics deferred):** the enqueue `session` (caller's, used to INSERT the Run) and the execution `db` Capability (worker-injected, used to RUN the Operation) are the same *type* but different *roles*, and must never be conflated. An Operation does **not** receive its `db` Capability at the enqueue call site — only at execution.

## Serialization binds the Operation, not just the queue

JSON-serializability / IDs-not-objects (ADR-0006) is a property of the **Operation's Data contract** (and its **output** type), enforced uniformly — not only at enqueue. Every transport crosses a serialization boundary (queue row, HTTP body, MCP tool args, CLI argv), so an Operation whose Data isn't serializable simply isn't projectable, which breaks the framework's one promise. v1 takes the uniformity tax on all Operations; lazy/per-projection enforcement is deferred unless the tax proves burdensome.

## Validation is two-phase; the second phase is Compile

Capability classification needs a capability registry that may not be populated when `@operation` runs at import. So validation phases:

- **Decoration (import time):** capture the signature, register identity, fail-fast on collision and structural/cron-tz rules.
- **Compile (startup):** resolve capabilities against the now-populated registry and fail-fast on the rest (a capability-typed param placed positionally, a keyword-only param that looks injectable but matches no registered capability, non-serializable Data). Idempotent; runs **implicitly on first app/worker start** (guaranteed backstop) and is also invocable **explicitly** (`app.compile()`) for tests/tooling. It produces an in-memory validated registry — no artifact (hence *compile*, not *build*). This generalizes the "fail fast at Worker/registry startup" check the system already does.

**Named seam:** the authoring contract owns *that* Compile validates and *when* it phases; the execution decision owns what populates the capability registry and what triggers Compile.

## Considered options

- **Layer rather than absorb** (keep `@task` as a queue-projection adapter over a core `@operation`) — rejected: it preserves the two-decorator confusion the VISION exists to dissolve; the developer should author one thing.
- **`@op` as the decorator name** — rejected: the core primitive deserves the full noun (it *is* the pitch), and `op` imports Dagster's "graph node" mental model. `op` survives only as the informal variable name.
- **Capability-by-explicit-marker** (`db: Capability[Session]` via `Annotated`) — viable fallback; rejected in favor of capability-by-registered-type + keyword-only, which keeps signatures ceremony-free. Revisit if a type registry proves too implicit.
- **Ambient (contextvar) enqueue session** — rejected: reintroduces dual-write ambiguity; the atomicity guarantee is worth one explicit argument.
- **Schedule/config decoupled onto `app.jobs.*`** or **a stacked `@job`/`@schedule` decorator** — both rejected for the three-way cut above (decoupling felt unintuitive; stacking resurrects a second decorator).
- **`seal` / `build`** as the name for the startup step — rejected: *seal* names only "closing," not the resolution+validation that happens; *build* implies a cached artifact this in-memory idempotent pass does not produce.

## Consequences

- The `@operation` primitive grows from the 23-line stub into the real authoring surface (parameterized decorator, signature parsing, `.enqueue` / `.schedule` projections, two-phase validation). `Registry`/`enqueue` converge onto it.
- `Run.task_namespace` / `task_name` rename to `operation_namespace` / `operation_name` — a free pre-release migration done before the schema ships. The `Task` *class* in code may keep its name until the execution work rewrites that surface, but human-facing text says Operation.
- "Task" is **retired** as domain vocabulary (banned like "job"); it survives only as an internal value object carrying the queue projection's execution config.
- Deferred to the execution decision (named, not designed here): capability construction/injection, the single-transaction injected-session model, effect recording/verification, the outbox, and what triggers Compile.
