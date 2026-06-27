# Serialization binds the Operation: JSON-serializable Data and output, IDs not objects

An Operation is authored once and *projected* onto many transports — the queue (a `Run` row), HTTP (a request/response body), MCP (tool args/result), the CLI (argv). Every one of those crosses a **serialization boundary**: the values handed in and the value handed back must survive a round-trip as JSON. We decided this is a property of the **Operation itself** — its **Data** (inputs) and its **output** type — not a quirk of any single projection. An Operation whose Data or output is not JSON-serializable simply isn't projectable, which breaks the framework's one promise that the same code reaches the queue, HTTP, MCP, and CLI without surprise.

The concrete rule the developer lives by: **pass IDs, not objects.** Type Data as ID newtypes (`CustomerId`), not live ORM rows; the Operation re-fetches what it needs through its injected `db` Capability at execution. The same constraint extends to the output type, because `Run.output` is JSONB, HTTP encodes it, and MCP returns it.

This ADR records the contract and how it is enforced. The vocabulary it produces (**Data**, the serialization boundary) lives in [CONTEXT.md](../../CONTEXT.md); the authoring decision that makes Data a property of the Operation is [ADR-0023](0023-operation-task-convergence.md).

## Why it binds the Operation, not the queue

The earlier mental model treated serializability as "an enqueue concern" — the queue stores a row, so the row's columns must be JSON. But enqueue is only one projection. HTTP, MCP, and CLI each serialize the same Data through a different boundary, and an Operation that can ride the queue but not HTTP would be a projection that silently isn't universal. Pinning the contract to the Operation keeps every projection honest by construction: validate the Data contract once, and every transport that carries it is safe.

Capabilities are deliberately exempt — they are *injected* per execution by a Provider, never serialized and never carried across a transport. The Data/Capability split (ADR-0023) is exactly the line between "must serialize" and "is constructed locally."

## Enforcement: uniform boundary guard plus a static lint

Two mechanisms share one definition of what serializes, so they cannot drift:

- **Runtime boundary guard.** At each serialization boundary the actual *values* are checked, and a non-serializable value fails with an error that names its location and points at this contract — not a cryptic encoder failure deep in a transport. The queue's input boundary is `op.enqueue` (the `inputs` JSONB); the output boundary is the worker writing `Run.output`. Output is rejected by **failing the Attempt** with the contract error, never by aborting the recording transaction — a raise there would leave the Run unrecorded and the Reaper would retry the same bad output forever.
- **Static lint, at Compile.** When a Data or output parameter is *annotated* with a known non-serializable type (e.g. a mapped ORM class), `app.compile()` reports it, so the mistake is caught before any Run is created rather than at the boundary at runtime.

Full *static proof* of serializability is not attempted — the lint only fails on known-bad annotations, and the runtime guard is the backstop for everything dynamic. The serializable set is pragmatic: JSON-native scalars plus the stdlib types that routinely encode to a JSON scalar (`date`/`datetime`/`time`/`timedelta`/`UUID`/`Decimal`). It exists to catch ORM objects and arbitrary classes, not to be a strict JSON purist.

## Scope

v1 takes the **uniformity tax** on all Operations: every Operation's Data and output must serialize, no exceptions. **Lazy / per-projection enforcement** — relaxing the rule for an Operation that is only ever reached through one in-process transport — is explicitly deferred, to be revisited only if the uniformity tax proves burdensome.

## Considered options

- **Serializability as an enqueue-only check** — rejected: it makes the queue projection safe while leaving HTTP/MCP/CLI to fail at their own boundaries, which defeats "author once, project anywhere."
- **Full static proof of serializability** — rejected: undecidable in general and hostile to dynamic values; the cheap lint plus a runtime guard catches the real mistake (an ORM object as Data) without pretending to a guarantee it can't make.
- **Allow ORM objects and serialize them automatically** — rejected: it reintroduces stale-snapshot and identity ambiguity across the enqueue/execute time gap; IDs-not-objects forces a fresh re-fetch through the execution `db` Capability.

## Consequences

- The contract is owned by a single module (`thrum.jobs.serialization`) that both the runtime guard and the Compile lint read, so enforcement is uniform across the data and output boundary by construction.
- Developers must type Data as IDs and re-fetch in the body. This is the intended ergonomic: the Operation body always works from durable identifiers, never a snapshot captured at enqueue.
- The deferred lazy/per-projection path is a known escape valve, not a redesign, if a future single-transport Operation makes the uniformity tax bite.
