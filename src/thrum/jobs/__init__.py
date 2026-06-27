"""thrum.jobs — the durable background-job slice (VISION §"first product slice").

A Postgres-native job runner: cron, queues, and (later) workflows as one
primitive (the Run), backed by your own Postgres. This is the framework's first
transport projection — "a job is an operation projected onto a queue." This
package re-exports only the lightweight SDK surface (Registry + Operation +
transactional enqueue). It must NOT import the Worker, the scheduler, or the
server at module load — see the import-discipline law in pyproject.toml.

Convergence (docs/adr/0023-operation-task-convergence.md): the authoring API is
`@operation` / `@registry.operation`. `@registry.task` is retired as authoring
vocabulary; the `Task` class survives only as an internal value object carrying
execution config the Worker reads.
"""

from thrum.jobs.app import App, CompileError
from thrum.jobs.enqueue import enqueue
from thrum.jobs.registry import (
    DeclaredSchedule,
    Operation,
    Registry,
    Task,
    operation,
)

__all__ = [
    "App",
    "CompileError",
    "DeclaredSchedule",
    "Operation",
    "Registry",
    "Task",
    "operation",
    "enqueue",
]
