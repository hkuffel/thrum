"""thrum.jobs — the durable background-job slice (VISION §"first product slice").

A Postgres-native job runner: cron, queues, and (later) workflows as one
primitive (the Run), backed by your own Postgres. This is the framework's first
transport projection — "a job is an operation projected onto a queue." This
package re-exports only the lightweight SDK surface (Task registry +
transactional enqueue). It must NOT import the Worker, the scheduler, or the
server at module load — see the import-discipline law in pyproject.toml and
docs/adr/0011-control-plane-api.md.

CONVERGENCE (decided — see docs/adr/0023-operation-task-convergence.md): the
`Task` / `@registry.task` surface below is **absorbed by `@operation`**. The
Operation is the sole authored unit; a job is an operation projected onto this
queue via `op.enqueue(session, **inputs)`. `Task` is retired as authoring
vocabulary and survives only as an internal value object carrying the queue
projection's execution config. The authoring contract (identity, the
data/capability split, three-way config, the enqueue-session seam, two-phase
validation via Compile) is fixed in ADR-0023 + docs/CONTEXT.md; the *execution*
machinery (capability injection, single-transaction commit, effect recording,
outbox) is the next, separately-decided step. This module is not yet rewritten
to the converged shape — ADR-0023 is the spec it grows into.
"""

from thrum.jobs.enqueue import enqueue
from thrum.jobs.registry import DeclaredSchedule, Registry, Task, task

__all__ = ["DeclaredSchedule", "Registry", "Task", "task", "enqueue"]
