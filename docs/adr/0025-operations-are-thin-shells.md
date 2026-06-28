# Operations are thin shells; reuse lives in capability-taking functions

ADR-0023 established that one authored `@operation` is *projected* onto many transports (queue, HTTP, MCP, CLI) — the framework's core pitch. This decides what happens when a transport needs something the others don't ("the HTTP edge needs an extra check"), so that the answer is never "write a second operation that is 95% a copy of the first." The decision: **the unit of code reuse below an Operation is a plain Python function that takes its capabilities as ordinary parameters.** Operations are thin, projectable *shells*; substance that more than one edge needs lives one level down, in normal functions. Operations do not call Operations.

This ADR fixes a *composition* contract. It builds on the authoring contract (ADR-0023) and the execution model (ADR-0024) and introduces no new runtime machinery — it is a rule about how user code is structured so the "one operation, many transports" promise survives contact with real divergence.

## The problem: three kinds of "difference" wear the same coat

The fear is that real users will fork operations because a transport "needed one extra line." On inspection, "an extra line for HTTP" is three different things, and only one of them is a composition problem at all:

- **Authorization** ("admins only on this route") — **not** operation logic. Handled by the **Caller** (born at the transport boundary) and **Capability** attenuation (ADR-0024 / CONTEXT.md): a more-restricted Caller yields a write-blocked or tenant-scoped `db`. No line in the body, no second operation. This is the *most common* "extra check," and it evaporates entirely.
- **Transport mechanics** (validate-and-return-400, idempotency key, rate limit, response encoding, sync-inline vs `202`+poll) — **not** operation logic. Handled by the **projection** at the App call site (`app.http.post(...)`, `app.tools.expose(...)`), never in the body. Already off-body by the Registry/App seam (ADR-0023).
- **Genuinely different behavior** ("when a human settles an invoice over HTTP, also send a receipt; the cron sweep must not") — the only real one. Projections do **not** save you here, and without a reuse unit below the Operation this is exactly where near-duplicate operations breed.

Capability design absorbs the first; projection design absorbs the second. This ADR is about the third.

## The decision

When two edges need *different behavior over shared substance*, factor the substance into a **plain function that takes its capabilities as ordinary arguments**, and let each thin Operation forward the capabilities the framework injected into it:

```python
# substance — NOT an operation, a normal function over its capabilities
def _settle(invoice: InvoiceId, db: Session) -> None:
    ...

@billing.operation
def settle_invoice(invoice: InvoiceId, *, db: Session) -> None:
    _settle(invoice, db)                      # the bare/cron/queue edge

@billing.operation
def settle_invoice_and_notify(invoice: InvoiceId, *, db: Session, mail: Mailer) -> None:
    _settle(invoice, db)                      # the HTTP edge that needs the extra line
    mail.send_receipt(invoice)
```

"Largely the same operation, twice" becomes "two thin shells over one function." The shared mass has exactly one home; the only thing written twice is the divergence — which is correct, because it is the only thing that actually differs.

Two rules make this hold:

1. **Operations are thin shells.** An Operation is a *boundary* object: identity (`namespace.name`), a projectable signature (Data + Capabilities), and the execution config that rides the decorator. It should contain orchestration, not a deep body. Depth belongs in plain functions it calls.
2. **Operations do not call Operations.** Reuse flows *down* into capability-taking functions, never *sideways* between Operations. This deliberately forecloses the question "when operation A calls operation B, is that one Run or two? one transaction or two? does B re-inject its capabilities?" — there is no good ambient answer, so we never ask it. `settle_invoice` calling `_settle` is an ordinary function call inside one Execution Scope's transaction; `settle_invoice` calling `settle_invoice_and_notify` is forbidden.

The body must also **never branch on transport** (`if transport == "http": ...`). A body that wants to know its transport is two operations wearing one; split it into shells. Transport-varying *inputs* arrive only as the Caller (auth) and Data (serialized per ADR-0006) — never as a transport tag.

## Why a plain function, not "a private operation"

A shared `@operation` would re-raise everything rule 2 exists to kill: it declares capabilities as injected params, but the framework — not the caller — injects capabilities (ADR-0024), so a caller invoking it would either hand-forge capabilities or trigger a nested injection/transaction. A plain function is *honest about needing a `Session`*: it takes `db` as a normal argument, and whichever shell calls it forwards the one the Execution Scope already constructed. This dovetails with ADR-0024's single-transaction model for free — `_settle` records its Effects into whichever scope's transaction it was handed, regardless of which Operation (and therefore which transport) drove it. Effect attribution stays by-construction.

It also keeps Operations honest as the *only* projectable surface: if substance could live in a non-projected `@operation`, the registry would fill with phantom identities that exist only to be called, muddying collision-detection and the dashboard's Run list. Plain functions have no identity and no Runs, which is exactly right for code that is not itself a unit of execution.

## Considered options

- **Allow Operations to call Operations** (treat the inner call as an inline sub-execution sharing the scope's transaction) — rejected. It demands an answer to sub-Run identity, nested capability injection, and transaction nesting that has no clean ambient form, and it tempts users to model *composition of behavior* as *composition of executions*, which is the workflow/DAG concern (a distinct, later primitive — see CONTEXT.md "Lineage"/DAG), not code reuse.
- **Solve divergence inside the body with a transport flag** (`if transport == "http"`) — rejected. It rots: the body stops being transport-neutral, projections stop being free, and every new transport edits every flagged body.
- **Per-projection "hook" callbacks** (let `app.http.post(op, before=..., after=...)` inject extra steps) — rejected for the behavior case. Hooks scatter business logic across projection call sites and run *outside* the operation's Execution Scope/transaction, breaking the atomic effects⊕outcome guarantee (ADR-0024). Composition belongs in a shell that runs *inside* the scope. (Transport-native middleware for *mechanics* — auth, rate limit — is fine; that is option two of the three-way split, not this.)
- **Accept the duplication** (let users copy operations) — rejected. It is the failure mode this ADR exists to prevent and silently erodes the "author once" pitch the moment a second transport ships.

## Consequences

- The guidance "keep Operation bodies thin; put reusable substance in plain functions that take capabilities as arguments" becomes a documented authoring convention (CONTEXT.md / SDK docs), not just folklore. Examples in docs should show the shell/function split for any non-trivial body.
- No runtime change. There is nothing to build: Operations already *are* callable functions, plain functions already take arguments, and the Execution Scope already owns the one transaction everything records into. This ADR prevents a future shape rather than adding one.
- The "operation calls operation" door stays closed until a real primitive (workflows/DAGs) opens it deliberately, with its own Run-and-transaction semantics — not as an accidental consequence of code reuse.
- The three-way split (auth → Caller/Capability, mechanics → projection, behavior → composition) is the canonical answer to "how do I do the transport-specific bit?" and should be the structure of that docs section.
