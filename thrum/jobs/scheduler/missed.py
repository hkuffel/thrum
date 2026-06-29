"""Missed-detection (ADR-0003 / CONTEXT.md): the state that defeats crontab
blindness.

A `scheduled` Run whose `fire_time` passed without it going `running` is the
central differentiator — a row in the wrong state, not an absent row nobody
notices. The sweep marks it `missed`, a first-class terminal state.

The gate is the Miss-detection Grace, an internal tolerance distinct from the
per-Schedule Start Grace SLO knob (CONTEXT.md). It is floored at one sweep interval
so ordinary scheduling jitter can never manufacture a false `missed`, and keeping
it separate from the SLO knob stops a tightened SLO from making this terminal
transition trigger-happy. The predicate evaluates DB-side against Postgres `now()`.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import func, update

from thrum.jobs.models import Run, RunStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def mark_missed(session: AsyncSession, miss_grace: dt.timedelta) -> int:
    """Mark every still-`scheduled` Run past `fire_time + miss_grace` as `missed`.
    Returns the number marked. Must run inside an open transaction.

    `fire_time < now() - miss_grace` is evaluated entirely DB-side so the decision
    uses the Postgres clock, not a Worker's wall clock."""
    result = await session.execute(
        update(Run)
        .where(Run.status == RunStatus.scheduled)
        .where(Run.fire_time < func.now() - miss_grace)
        .values(status=RunStatus.missed)
    )
    return result.rowcount
