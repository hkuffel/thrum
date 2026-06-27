"""Missed-detection (ADR-0003 / CONTEXT.md): the state that defeats crontab
blindness.

A `scheduled` Run whose `fire_time` passed without it transitioning to `running`
is the central differentiator made observable — a row in the wrong state, not an
absent row nobody notices. The sweep marks it `missed`: a first-class terminal
lifecycle state, not a derived inference.

The gate is the Miss-detection Grace, an internal operational tolerance, not the
per-Schedule Start Grace SLO knob (CONTEXT.md). It is floored at ≥ one sweep
interval (`SchedulerConfig.effective_miss_grace`) so ordinary scheduling jitter —
a Run claimable-but-not-yet-claimed when a sweep fires — can never manufacture a
false `missed`. Decoupling it from the SLO knob means tightening an SLO can never
make this hard terminal transition trigger-happy.

The predicate is evaluated DB-side against Postgres `now()`; a Run already claimed
(flipped to `running` by the `scheduled` arm of Claim) before the grace elapses no
longer matches `status = 'scheduled'` and is safe.
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
