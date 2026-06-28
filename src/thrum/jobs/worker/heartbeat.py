"""Heartbeat (CONTEXT.md / ADR-0013): the periodic renewal of a running Run's
`lease_expires_at` by its executing Worker, proving liveness.

One coroutine per Worker, not per Run: each tick bumps the lease for all of this
Worker's open Attempts (`claimed_by = self AND ended_at IS NULL`) in one short
transaction. It runs on a dedicated connection (separate from execution sessions
and the leader's advisory-lock connection) so an Operation holding a session open
cannot starve lease renewal. Cadence is `lease_ttl / 3` (config knob), so two
consecutive missed renewals are required before a live Run looks orphaned — slack
against a momentarily busy loop without slowing real-orphan detection.

Known limitation (ADR-0013): a sync/CPU-bound Operation that blocks the event loop
can starve this coroutine and get itself falsely reaped → double execution.
Accepted for now; at-least-once execution is the correctness backstop, and
blocking Operations belong on the thread/process-pool path, not yet implemented.
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
