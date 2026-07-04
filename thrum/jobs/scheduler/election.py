from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

SWEEP_LOCK_KEY = 0x746872756D


async def try_acquire_sweep_lock(conn: AsyncConnection) -> bool:
    result = await conn.execute(select(func.pg_try_advisory_lock(SWEEP_LOCK_KEY)))
    return bool(result.scalar_one())


async def release_sweep_lock(conn: AsyncConnection) -> bool:
    result = await conn.execute(select(func.pg_advisory_unlock(SWEEP_LOCK_KEY)))
    return bool(result.scalar_one())
