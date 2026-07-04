from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import func, update

from thrum.jobs.models import Run, RunStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def mark_missed(session: AsyncSession, miss_grace: dt.timedelta) -> int:
    result = await session.execute(
        update(Run)
        .where(Run.status == RunStatus.scheduled)
        .where(Run.fire_time < func.now() - miss_grace)
        .values(status=RunStatus.missed)
    )
    return result.rowcount
