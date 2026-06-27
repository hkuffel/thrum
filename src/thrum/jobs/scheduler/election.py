"""Leader election (ADR-0007): the Scheduler is a role, not a process. Every
Worker opportunistically contends for a single session-scoped Postgres advisory
lock on its own dedicated connection; the winner runs the reconciliation sweep
(reap, missed-detection, materialize) and the rest are warm failover.

Session-scoped (`pg_try_advisory_lock`, not the `_xact_` variant) is the whole
point: the lock is held for the connection's life, independent of transactions.
The leader holds its connection open for its whole life, so if it dies, Postgres
drops the connection and releases the lock, and the next Worker wins on its next
`try` — self-healing failover with no lease, no timeout, no heartbeat of its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection

# A fixed bigint key identifying Thrum's single sweep-leadership lock. Arbitrary
# but stable — derived from "thrum" so it is unlikely to collide with an
# application's own advisory-lock usage in the same database.
SWEEP_LOCK_KEY = 0x746872756D  # b"thrum"


async def try_acquire_sweep_lock(conn: AsyncConnection) -> bool:
    """Attempt to acquire the session-scoped sweep lock on `conn`. Returns True if
    this connection now holds it (either just acquired or already held — the call
    is safe to repeat). Non-blocking: returns False immediately if another
    connection holds it."""
    result = await conn.execute(select(func.pg_try_advisory_lock(SWEEP_LOCK_KEY)))
    return bool(result.scalar_one())


async def release_sweep_lock(conn: AsyncConnection) -> bool:
    """Release the sweep lock held by `conn`. Returns True if a lock was released.
    Normally never called — the leader holds its lock for its whole life and lets
    connection death release it — but explicit release keeps the lock testable and
    supports a clean voluntary step-down."""
    result = await conn.execute(select(func.pg_advisory_unlock(SWEEP_LOCK_KEY)))
    return bool(result.scalar_one())
