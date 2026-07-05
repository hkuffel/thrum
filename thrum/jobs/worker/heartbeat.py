"""Heartbeat — extending the Lease on a Worker's open Attempts."""

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
    """Push the Lease expiry forward on every open Attempt this Worker holds.

    The new expiry is computed from the DB clock so all Workers and the Reaper
    agree on liveness.

    Returns:
        The number of Leases renewed.
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
