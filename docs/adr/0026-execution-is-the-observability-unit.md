# Observability attaches to the Execution; durability to the Run

## Context

Effect recording lived on the durable path only: the recorder was wired into the
Worker's Execution Scope and `effects` keyed off the **Attempt**. So an Operation
reached through a *synchronous* projection (HTTP/MCP/CLI) would mutate state with
**no Effect recorded** — invisible to the very observability the framework exists
to provide. That is the dual of the cron silent-failure wedge: an "HTTP 200 that
changed nothing" is exactly as dangerous as a green job that did nothing, and we
were blind to it.

## Decision

Split two axes that were conflated:

- **Run** — the signature of a *durable* projection (queue/timer/workflow). It is
  about **durability**: claim, lease, heartbeat, reap, retry, missed-detection.
  Synchronous projections create no Run.
- **Execution** — one running of an Operation through the **Execution Scope**
  (decode → construct Capabilities → invoke → record → commit). It is the unit
  **observability** attaches to. An **Attempt is the durable kind of Execution**
  (one belonging to a Run); a synchronous projection produces an Execution with
  no Run.

**Effects key off the Execution, not the Attempt/Run**, and the recorder lives in
the shared **Execution Scope** rather than the Worker. Therefore a synchronous
Execution records Effects too, and the **Zero-Effect** wedge applies uniformly to
HTTP, MCP, and CLI — not just queued jobs.

## Consequences

- `effects.attempt_id` becomes `execution_id`; effect recording moves out of the
  Worker into the (now transport-shared) Execution Scope.
- A mutating synchronous Operation becomes observable and subject to Zero-Effect.
- An Execution is persisted only when it carries observable weight (an Effect, a
  failure, or a durability obligation — i.e. it is an Attempt); a pure successful
  read persists nothing, so introspection does not flood Postgres.

## Considered and rejected

- **Record only on the durable path** — leaves every synchronous mutation
  invisible; defeats the silent-failure thesis the moment HTTP exists.
- **Emit synchronous Executions to OTel only** — bifurcates observability into two
  differently-shaped records and puts the Zero-Effect wedge out of the dashboard's
  reach for HTTP. We want one record shape and one wedge across all transports.
