"""Heartbeat (CONTEXT.md / ADR-0013): renew a running Run's lease to prove liveness.

One coroutine per Worker, not per Run: each tick bumps `lease_expires_at` for all
of this Worker's open Attempts in one short transaction, on a dedicated connection
so an Operation holding its session open cannot starve renewal. Cadence is
`lease_ttl / 3`, so two missed renewals are needed before a live Run looks
orphaned.

A sync, CPU-bound Operation that blocks the loop can still starve this coroutine
and be falsely reaped into double execution (ADR-0013); at-least-once execution is
the backstop until the thread/process-pool path exists.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update

from thrum.jobs.models import Attempt

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def renew_leases(
    session: AsyncSession,
    worker_id: str,
    lease_ttl: dt.timedelta,
) -> int:
    """Renew `lease_expires_at = now() + lease_ttl` for every open Attempt this
    Worker holds. Returns the number of Attempts renewed. Must run inside an open
    transaction.

    The base instant is Postgres `now()`; the TTL is added to it, mirroring claim —
    there is no Worker wall-clock in the stored value. A closed Attempt (`ended_at`
    set) is never matched, so it is never renewed.
    """
    db_now = (await session.execute(select(func.now()))).scalar_one()
    new_expiry = db_now + lease_ttl
    result = await session.execute(
        update(Attempt)
        .where(Attempt.claimed_by == worker_id)
        .where(Attempt.ended_at.is_(None))
        .values(lease_expires_at=new_expiry)
    )
    return result.rowcount
