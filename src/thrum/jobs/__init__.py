"""thrum.jobs — the durable background-job slice (VISION §"first product slice").

A Postgres-native job runner: cron, queues, and (later) workflows as one
primitive (the Run), backed by your own Postgres. This is the framework's first
transport projection — "a job is an operation projected onto a queue." This
package re-exports only the lightweight SDK surface (Registry + Operation +
transactional enqueue). It must NOT import the Worker, the scheduler, or the
server at module load — see the import-discipline law in pyproject.toml.

The authoring API is `@operation` / `@registry.operation` (ADR-0023): one
authored primitive, projected onto the queue via `op.enqueue(...)`.
"""

from thrum.jobs.app import App, CompileError
from thrum.jobs.enqueue import enqueue
from thrum.jobs.registry import (
    DeclaredSchedule,
    Operation,
    Registry,
    operation,
)

__all__ = [
    "App",
    "CompileError",
    "DeclaredSchedule",
    "Operation",
    "Registry",
    "operation",
    "enqueue",
]
