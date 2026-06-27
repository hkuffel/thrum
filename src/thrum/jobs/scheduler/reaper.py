"""Reaper (CONTEXT.md / ADR-0013 / ADR-0020): the orphan-recovery duty of the
leader's reconciliation sweep.

An orphan is an open Attempt (`ended_at IS NULL`) whose lease has lapsed
(`lease_expires_at < now()`) — its Worker stopped heartbeating, presumed dead.
The Reaper closes each such Attempt `abandoned` and returns its parent Run to a
claimable state (`pending`, `next_attempt_at = now()`) unconditionally: a dead or
rolling-deployed Worker is infrastructure failure, never the Task failing, so it
must not burn a retry (ADR-0020). The Attempt budget counts only
`failed`/`timed_out`, which the Reaper never produces, so it computes no budget.

All clock comparisons evaluate DB-side against Postgres `now()`, so there is no
Worker-wall-clock skew in deciding what is lapsed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from thrum.jobs.models import Attempt, AttemptOutcome, Run, RunStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def reap_orphans(session: AsyncSession) -> int:
    """Reap every open Attempt past its lease: close it `abandoned` and requeue
    its Run to `pending`/`next_attempt_at = now()`. Returns the number reaped.
    Must run inside an open transaction.

    `SKIP LOCKED` makes the pass safe even if two leaders briefly overlap during a
    handoff; in steady state exactly one leader runs it. A live Attempt — one a
    healthy Worker is still heartbeating — never matches the predicate, so it is
    left untouched (no false reap, modulo the accepted loop-starvation window in
    ADR-0013).
    """
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
        if run is not None:  # pragma: no branch - FK guarantees a parent Run
            # Unconditional requeue (ADR-0020): no budget computation here.
            run.status = RunStatus.pending
            run.next_attempt_at = db_now

    await session.flush()
    return len(orphans)
