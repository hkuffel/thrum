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
    operation_key: str  # namespace.name
    inputs: dict
    caller: dict | None  # the Caller frozen at Enqueue, thawed by the Scope


async def claim_runs(
    session: AsyncSession,
    worker_id: str,
    limit: int,
    lease_ttl: dt.timedelta,
) -> list[ClaimedRun]:
    """Claim up to `limit` due Runs. Must run inside an open transaction."""
    if limit <= 0:
        return []

    # Derive the lease window from the DB clock, not the Worker's wall clock
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
        prior = (
            await session.execute(
                select(func.coalesce(func.max(Attempt.attempt_number), 0)).where(
                    Attempt.run_id == run.id
                )
            )
        ).scalar_one()
        # Attempt numbers are per-Run and monotonic
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
                operation_key=f"{run.operation_namespace}.{run.operation_name}",
                inputs=dict(run.inputs or {}),
                caller=dict(run.caller) if run.caller else None,
            )
        )
    return claimed
