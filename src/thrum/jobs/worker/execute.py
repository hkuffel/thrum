"""Execute: resolve a claimed Run's Task and run its code.

Pure with respect to the database — it takes a ClaimedRun and a task lookup,
invokes the user's `fn`, and returns a structured outcome. Async context only
this slice (ADR-0005's thread-pool / process-pool routing is out of scope).

PORT SEAM (net-new, deferred — see docs/VISION.md lines 220-226, 242):
This pure, out-of-transaction execution is the insertion point for thrum's
single-transaction, injected-session model. The target replaces this step so the
operation runs *inside* one framework-owned transaction with a framework-
constructed, instrumented SQLAlchemy session capability; effect-recording and the
completion `record` then commit atomically with the operation's own DB effects,
making "succeeded but produced zero effects" detectable. The port keeps this pure
version working as-is; building the injected-session execution is the user's next
step, not part of the port.
"""

from __future__ import annotations

import inspect
import traceback
from dataclasses import dataclass
from typing import TYPE_CHECKING

from thrum.jobs.models import AttemptOutcome

if TYPE_CHECKING:
    from thrum.jobs.registry import Task
    from thrum.jobs.worker.claim import ClaimedRun


@dataclass(frozen=True)
class ExecutionResult:
    outcome: AttemptOutcome  # succeeded | failed (this slice)
    output: dict | None
    error: str | None  # traceback on failure


async def execute_run(claimed: ClaimedRun, tasks: dict[str, Task]) -> ExecutionResult:
    """Resolve the Task by `namespace.name` and invoke it. A resolution miss or a
    raised exception both produce a `failed` outcome with a captured message —
    never propagated, so the Worker loop keeps turning."""
    task = tasks.get(claimed.task_key)
    if task is None:
        return ExecutionResult(
            outcome=AttemptOutcome.failed,
            output=None,
            error=f"Task {claimed.task_key!r} is not registered in this Worker",
        )

    try:
        result = task.fn(**claimed.inputs)
        if inspect.isawaitable(result):
            result = await result
    except Exception:
        return ExecutionResult(
            outcome=AttemptOutcome.failed,
            output=None,
            error=traceback.format_exc(),
        )

    # JSONB output column holds a dict; richer return-value capture is later work.
    output = result if isinstance(result, dict) else None
    return ExecutionResult(outcome=AttemptOutcome.succeeded, output=output, error=None)
