"""Record: close the Attempt and either retry the Run or set it terminal.

Runs in its own short transaction, after execution, so no DB transaction is held
open while the Task runs. On a Task-attributable failure (`failed`/`timed_out`)
Record consults the Task's retry policy: while the Attempt budget remains it
returns the Run to `pending` with a backed-off `next_attempt_at` (ADR-0021), and
only on budget exhaustion sets the Run terminal `failed`. The budget is counted
from Task-attributable Attempt outcomes, never from `attempt_number`, so a reaped
or requeued Worker death never consumes a retry (ADR-0020). The count and the
`pending` + `next_attempt_at` write share this one transaction, so a retry is
never half-committed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from thrum.jobs.models import Attempt, AttemptOutcome, Run, RunStatus
from thrum.jobs.retry import RetryPolicy, backoff_delay

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from thrum.jobs.registry import Task
    from thrum.jobs.worker.claim import ClaimedRun
    from thrum.jobs.worker.execute import ExecutionResult

# Outcomes that spend the Attempt budget — the Task's own fault (ADR-0020).
# `abandoned`/`requeued` (the Worker, not the Task) are deliberately excluded.
_BUDGETED = (AttemptOutcome.failed, AttemptOutcome.timed_out)

# An unresolved Task (not registered in this Worker) cannot be retried meaningfully
# — treat it as a single, non-retryable attempt so the misconfiguration surfaces
# as a terminal `failed` rather than hot-looping (ADR-0021).
_NO_RETRY = RetryPolicy(max_attempts=1)


async def record_result(
    session: AsyncSession,
    claimed: ClaimedRun,
    result: ExecutionResult,
    task: Task | None = None,
) -> None:
    """Close the Attempt with its outcome, then move the Run to its next state:
    `succeeded`, a backed-off `pending` retry, or terminal `failed` once the budget
    is spent. Must run inside an open transaction."""
    run = await session.get(Run, claimed.run_id)
    attempt = await session.get(Attempt, claimed.attempt_id)
    if run is None or attempt is None:  # pragma: no cover - defensive
        return

    db_now = (await session.execute(select(func.now()))).scalar_one()
    attempt.ended_at = db_now
    attempt.outcome = result.outcome
    attempt.error = result.error

    if result.outcome == AttemptOutcome.succeeded:
        run.status = RunStatus.succeeded
        run.output = result.output
        await session.flush()
        return

    # Task-attributable failure: retry while the budget allows (ADR-0020/0021).
    policy = task.retry_policy if task is not None else _NO_RETRY
    await session.flush()  # make this Attempt's outcome visible to the budget count
    failures = (
        await session.execute(
            select(func.count())
            .select_from(Attempt)
            .where(Attempt.run_id == run.id)
            .where(Attempt.outcome.in_(_BUDGETED))
        )
    ).scalar_one()

    if failures >= policy.max_attempts:
        run.status = RunStatus.failed
    else:
        # `failures` is 1 after the first failure, so it is exactly the
        # `failure_index` the backoff curve expects (1-indexed).
        run.status = RunStatus.pending
        run.next_attempt_at = db_now + backoff_delay(failures, policy)

    await session.flush()
