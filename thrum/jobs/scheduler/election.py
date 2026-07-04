"""Leader election: the Scheduler is a role, not a process. Every
Worker opportunistically contends for a single session-scoped Postgres advisory
lock on its own dedicated connection; the winner runs the reconciliation sweep
(reap, missed-detection, materialize) and the rest are warm failover. More on this
in ADR-0007.

The leader holds its connection open for its whole life, so if it dies, Postgres
drops the connection and releases the lock, and the next Worker wins on its next
`try` — self-healing failover with no lease, no timeout, no heartbeat of its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

# A fixed bigint key identifying Thrum's single sweep-leadership lock.
SWEEP_LOCK_KEY = 0x746872756D  # b"thrum"


async def try_acquire_sweep_lock(conn: AsyncConnection) -> bool:
    """Attempt to acquire the session-scoped sweep lock on `conn`.
    Non-blocking: returns False immediately if another connection holds it.
    Safe to repeat."""
    result = await conn.execute(select(func.pg_try_advisory_lock(SWEEP_LOCK_KEY)))
    return bool(result.scalar_one())


async def release_sweep_lock(conn: AsyncConnection) -> bool:
    """Release the sweep lock held by `conn`. Normally never called,
    the leader holds its lock for its whole life and lets
    connection death release it"""
    result = await conn.execute(select(func.pg_advisory_unlock(SWEEP_LOCK_KEY)))
    return bool(result.scalar_one())
