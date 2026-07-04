from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from thrum.jobs.models import Attempt, AttemptOutcome, Run, RunStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def reap_orphans(session: AsyncSession) -> int:
    orphans = (
        (
            await session.execute(
                select(Attempt)
                .where(Attempt.ended_at.is_(None))
                .where(Attempt.lease_expires_at < func.now())
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    if not orphans:
        return 0

    db_now = (await session.execute(select(func.now()))).scalar_one()
    for attempt in orphans:
        attempt.ended_at = db_now
        attempt.outcome = AttemptOutcome.abandoned
        run = await session.get(Run, attempt.run_id)
        if run is not None:
            run.status = RunStatus.pending
            run.next_attempt_at = db_now

    await session.flush()
    return len(orphans)
