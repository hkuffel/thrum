"""Claim: the dispatch step (ADR-0008 / ADR-0013).

`SELECT … FOR UPDATE SKIP LOCKED` up to a capacity, then in the *same* short
transaction insert an Attempt stamped with the lease and flip the Run to
`running`. The caller owns the transaction boundary and must commit promptly so
the row locks release before execution — the lease, not the row lock, governs
liveness while the Task runs.

Both arms of the claimability predicate are live (core-loop Q2 / ADR-0008):
    (status = 'pending'   AND next_attempt_at <= now())  -- enqueues + retries
    OR (status = 'scheduled' AND fire_time   <= now())   -- cron occurrences
A due `scheduled` cron occurrence is flipped **directly** to `running` (PRD 0003)
— no separate promotion step, whose downtime would reintroduce scheduler-down
blindness. All time comparisons evaluate DB-side against Postgres `now()`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import and_, func, or_, select

from thrum.jobs.models import Attempt, Run, RunStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ClaimedRun:
    """The handoff payload from Claim to Execute/Record — plain data, not a live
    ORM object, so it survives the claim transaction closing."""

    run_id: uuid.UUID
    attempt_id: uuid.UUID
    task_key: str  # namespace.name
    inputs: dict


async def claim_runs(
    session: AsyncSession,
    worker_id: str,
    limit: int,
    lease_ttl: dt.timedelta,
) -> list[ClaimedRun]:
    """Claim up to `limit` due Runs. Must run inside an open transaction."""
    if limit <= 0:
        return []

    # Postgres is the clock authority (core-loop Q5): derive the lease window from
    # the DB clock, not the Worker's wall clock.
    db_now = (await session.execute(select(func.now()))).scalar_one()
    lease_expires_at = db_now + lease_ttl

    stmt = (
        select(Run)
        .where(
            or_(
                and_(
                    Run.status == RunStatus.pending,
                    or_(Run.next_attempt_at.is_(None), Run.next_attempt_at <= func.now()),
                ),
                and_(Run.status == RunStatus.scheduled, Run.fire_time <= func.now()),
            )
        )
        .order_by(Run.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    runs = (await session.execute(stmt)).scalars().all()

    claimed: list[ClaimedRun] = []
    for run in runs:
        # Attempt numbers are per-Run and monotonic. A reaped Run (ADR-0013/0020)
        # is returned to `pending` and re-claimed, so its second try must not
        # reuse number 1 — that would collide on uq_attempts_run_number. Retry/
        # backoff (a later slice) drives the same increment for genuine failures.
        prior = (
            await session.execute(
                select(func.coalesce(func.max(Attempt.attempt_number), 0)).where(
                    Attempt.run_id == run.id
                )
            )
        ).scalar_one()
        attempt = Attempt(
            run_id=run.id,
            attempt_number=prior + 1,
            claimed_by=worker_id,
            started_at=db_now,
            lease_expires_at=lease_expires_at,
        )
        session.add(attempt)
        run.status = RunStatus.running
        await session.flush()  # populate attempt.id
        claimed.append(
            ClaimedRun(
                run_id=run.id,
                attempt_id=attempt.id,
                task_key=f"{run.task_namespace}.{run.task_name}",
                inputs=dict(run.inputs or {}),
            )
        )
    return claimed
