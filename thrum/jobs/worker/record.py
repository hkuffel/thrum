"""Result recording — closing an Attempt and deciding the Run's next state.

Applies the retry budget: a success finalizes the Run, a failure either exhausts
the budget (``failed``) or schedules a backoff retry (``pending``). Only
Operation-attributable outcomes spend the budget — a Worker death does not.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from thrum.jobs.models import Attempt, AttemptOutcome, Run, RunStatus
from thrum.jobs.retry import RetryPolicy, backoff_delay

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from thrum.jobs.registry import Operation
    from thrum.jobs.worker.claim import ClaimedRun
    from thrum.jobs.worker.execute import ExecutionResult

# Outcomes that spend a retry: the Operation's own fault. abandoned and requeued
# are Worker-attributable and cost nothing (ADR-0020).
_BUDGETED = (AttemptOutcome.failed, AttemptOutcome.timed_out)

_NO_RETRY = RetryPolicy(max_attempts=1)


async def record_result(
    session: AsyncSession,
    claimed: ClaimedRun,
    result: ExecutionResult,
    operation: Operation | None = None,
) -> None:
    """Close the Attempt and set the Run's next lifecycle state.

    On success the Run is finalized with its output. On failure the budgeted
    failure count decides between permanent ``failed`` and a ``pending`` retry
    scheduled after a backoff delay. A per-Run ``max_attempts`` overrides the
    Operation's policy. A no-op if the Run or Attempt has vanished.

    Args:
        operation: The Operation, for its retry policy; ``None`` falls back to
            no-retry.
    """
    run = await session.get(Run, claimed.run_id)
    attempt = await session.get(Attempt, claimed.attempt_id)
    if run is None or attempt is None:
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

    policy = operation.retry_policy if operation is not None else _NO_RETRY
    if run.max_attempts is not None:
        policy = dataclasses.replace(policy, max_attempts=run.max_attempts)
    # Flush first so this Attempt's just-set outcome is included in the count.
    await session.flush()
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
        run.status = RunStatus.pending
        run.next_attempt_at = db_now + backoff_delay(failures, policy)

    await session.flush()
