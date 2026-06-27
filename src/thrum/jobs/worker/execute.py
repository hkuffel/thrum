"""Execute: resolve a claimed Run's Task and run its code.

Pure with respect to the database — it takes a ClaimedRun and a task lookup,
invokes the user's `fn`, and returns a structured outcome. Async context only for
now; ADR-0005's thread-pool / process-pool routing is out of scope.

This pure, out-of-transaction execution is the seam for the single-transaction
injected-session model (ADR-0024): that model replaces this step so the operation
runs inside one framework-owned transaction with an instrumented SQLAlchemy
session, and effect-recording plus the completion `record` then commit atomically
with the operation's own DB effects, making "succeeded but produced zero effects"
detectable. Not yet implemented; this pure version is the current behavior.
"""

from __future__ import annotations

import inspect
import traceback
from dataclasses import dataclass
from typing import TYPE_CHECKING

from thrum.jobs.models import AttemptOutcome
from thrum.jobs.serialization import SerializationContractError, ensure_serializable

if TYPE_CHECKING:
    from thrum.jobs.registry import Task
    from thrum.jobs.worker.claim import ClaimedRun


@dataclass(frozen=True)
class ExecutionResult:
    outcome: AttemptOutcome  # succeeded or failed
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

    # The JSONB output column holds a dict; non-dict returns are dropped for now.
    output = result if isinstance(result, dict) else None

    # The completion serialization boundary: `Run.output` is JSONB, so a
    # non-serializable output fails this Attempt with the contract error rather
    # than aborting the recording transaction — a raise there would leave the
    # Run unrecorded and the Reaper would retry the same bad output forever.
    try:
        ensure_serializable(output, owner=claimed.task_key, role="output")
    except SerializationContractError as exc:
        return ExecutionResult(
            outcome=AttemptOutcome.failed, output=None, error=str(exc)
        )

    return ExecutionResult(outcome=AttemptOutcome.succeeded, output=output, error=None)
