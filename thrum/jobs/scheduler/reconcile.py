"""Reconciling code-declared Schedules against their durable rows.

Startup asserts each declared Schedule into the table and stamps it seen; a
Schedule that stops being declared goes stale and is deactivated by the sweep,
so removing a ``schedule(...)`` from code stops future materialization without a
manual pause.
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
    """Upsert the currently declared Schedules and stamp each as freshly seen.

    Reactivates a previously stale row when its declaration returns, and updates
    the timing policy to match code.

    Args:
        declared: Declared Schedules keyed by Operation ``namespace.name``.

    Returns:
        The number of rows inserted or updated.
    """
    if not declared:
        return 0

    rows = []
    for key, specs in declared.items():
        ns, name = key.split(".", 1)
        for spec in specs:
            rows.append(
                {
                    "operation_namespace": ns,
                    "operation_name": name,
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
        constraint="uq_schedules_operation_cron",
        set_={
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
    """Deactivate Schedules not re-declared within ``stale_threshold``.

    This is the code-declaration side of active state, distinct from an
    operator's manual pause.

    Returns:
        The number of Schedules deactivated.
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
