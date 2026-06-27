"""Schedule reconciliation (ADR-0022): converge the `schedules` table on what the
live Worker fleet declares in code, and gate off declarations no Worker still
asserts. Both functions touch only the declaration gate, never the operational
gate, so an operator pause survives reconciliation.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from thrum.jobs.models import Schedule

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from thrum.jobs.registry import DeclaredSchedule


async def assert_declared_schedules(
    session: AsyncSession,
    declared: dict[str, list[DeclaredSchedule]],
) -> int:
    """Persist the declared Schedule set into the `schedules` table.

    Keyed on Schedule identity (task_namespace, task_name, cron) so an
    operation may declare several recurrences without them colliding. INSERT …
    ON CONFLICT DO UPDATE so a changed tz/policy updates the matching
    recurrence in place, a new recurrence inserts, and a re-declared one
    revives its declaration gate.

    Writes only the declaration gate — never the operational gate (ADR-0022).
    Returns the number of rows upserted.
    """
    if not declared:
        return 0

    rows = []
    for key, specs in declared.items():
        ns, name = key.split(".", 1)
        for spec in specs:
            rows.append(
                {
                    "task_namespace": ns,
                    "task_name": name,
                    "cron": spec.cron,
                    "timezone": spec.timezone,
                    "declared_duration": spec.declared_duration,
                    "sla": spec.sla,
                    "start_grace": spec.start_grace,
                    "declaration_active": True,
                    "last_declared_at": func.now(),
                }
            )

    stmt = pg_insert(Schedule).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_schedules_task_cron",
        set_={
            # `cron` is part of the conflict key, so it never changes on update.
            "timezone": stmt.excluded.timezone,
            "declared_duration": stmt.excluded.declared_duration,
            "sla": stmt.excluded.sla,
            "start_grace": stmt.excluded.start_grace,
            "declaration_active": stmt.excluded.declaration_active,
            "last_declared_at": stmt.excluded.last_declared_at,
        },
    )
    result = await session.execute(stmt)
    return result.rowcount


async def pause_stale_schedules(
    session: AsyncSession,
    stale_threshold: dt.timedelta,
) -> int:
    """Clear the declaration gate for Schedules no live Worker still declares.

    A Schedule is stale when last_declared_at < now() - stale_threshold AND
    declaration_active is still True. During rolling deploys old Workers keep
    stamping last_declared_at, so a Schedule is only gated off once no Worker
    has declared it for the full threshold window.

    Writes only the declaration gate — never the operational gate (ADR-0022).
    Returns the number of rows paused.
    """
    stmt = (
        update(Schedule)
        .where(
            Schedule.declaration_active.is_(True),
            Schedule.last_declared_at < func.now() - stale_threshold,
        )
        .values(declaration_active=False)
    )
    result = await session.execute(stmt)
    return result.rowcount
