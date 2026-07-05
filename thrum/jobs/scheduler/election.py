"""Scheduler election — the advisory lock that makes one Worker the Scheduler."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

# The advisory lock id, ASCII "thrum". Only its uniqueness matters; the value is
# arbitrary but stable so every Worker contends for the same lock.
SWEEP_LOCK_KEY = 0x746872756D


async def try_acquire_sweep_lock(conn: AsyncConnection) -> bool:
    """Try to become the Scheduler without blocking.

    Returns:
        True if this connection now holds the sweep lock. The lock is held until
        released or the session ends, so other Workers are warm failover.
    """
    result = await conn.execute(select(func.pg_try_advisory_lock(SWEEP_LOCK_KEY)))
    return bool(result.scalar_one())


async def release_sweep_lock(conn: AsyncConnection) -> bool:
    """Release the sweep lock, letting another Worker take the Scheduler role."""
    result = await conn.execute(select(func.pg_advisory_unlock(SWEEP_LOCK_KEY)))
    return bool(result.scalar_one())
