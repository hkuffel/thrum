# The Control Plane is operations projected onto transports, not a hub API

## Context

The earlier `Control Plane` definition was a hub topology: "a single
(containerized FastAPI) API… the dashboard, the `--json` CLI, and the MCP server
are all thin clients of it." That predates "operations all the way down." It would
force the CLI and MCP to become HTTP clients of one API, lose the property that
`thrum runs list` works against a bare Postgres with nothing running, and put
FastAPI at the center of a framework meant to obsolete it.

## Decision

Observability — and command — is **operations all the way down**. Thrum serves the
data its execution substrate writes by shipping **built-in observe-and-command
operations** and **projecting them onto transports**, exactly like user
operations. HTTP, the `--json` CLI, and the MCP server are **co-equal projections**
of those operations, not clients of one API. The **Control Plane** is that set of
built-in operations plus the shared **Execution Scope** they run through.

Two consequences that are really one principle — *durability and reach are
properties of the projection, not the operation*:

- **Durability follows the projection.** A **Run** exists iff an operation is
  reached through a *durable* projection (queue/timer/workflow). Synchronous
  projections (HTTP/MCP/CLI) run the operation inline and create no Run. Read-only
  introspection is Run-less because it is reached synchronously — not because it is
  read-only.
- **The chokepoint is the scope, not a network door.** Validation, RBAC, audit,
  and the OSS/Pro line are enforced at the shared Execution Scope every projection
  funnels through. There is no single local network boundary, and that is fine: the
  logical chokepoint is the scope. The hosted (Pro) plane *deploys* the HTTP
  projection as a centralized service — a deployment topology of the paid product,
  not the definition of the Control Plane.

## Consequences

- The read-only Control Plane is built as real operations (`thrum.list_runs`,
  `thrum.get_run`), authored against a read-only `db` capability — never a bespoke
  reader. Devs customize observability by authoring their own operations over the
  same durable records.
- `thrum runs list` connects straight to the dev's Postgres and works with no
  server running.
- The framework dogfoods its own primitive: Thrum observes Thrum through the same
  mechanism users observe their apps.

## Considered and rejected

- **Hub API (FastAPI center, CLI/MCP as HTTP clients)** — bifurcates the model,
  breaks the no-server-needed CLI, and centers the framework on the thing it
  replaces.
- **Bespoke read-only reader refactored into operations later** — puts FastAPI
  sludge in the core and makes the framework's own observation surface a different
  kind of thing than user operations.
