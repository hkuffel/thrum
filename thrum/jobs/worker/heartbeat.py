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
    db_now = (await session.execute(select(func.now()))).scalar_one()
    new_expiry = db_now + lease_ttl
    result = await session.execute(
        update(Attempt)
        .where(Attempt.claimed_by == worker_id)
        .where(Attempt.ended_at.is_(None))
        .values(lease_expires_at=new_expiry)
    )
    return result.rowcount
