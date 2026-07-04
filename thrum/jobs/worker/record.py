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

_BUDGETED = (AttemptOutcome.failed, AttemptOutcome.timed_out)

_NO_RETRY = RetryPolicy(max_attempts=1)


async def record_result(
    session: AsyncSession,
    claimed: ClaimedRun,
    result: ExecutionResult,
    operation: Operation | None = None,
) -> None:
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
