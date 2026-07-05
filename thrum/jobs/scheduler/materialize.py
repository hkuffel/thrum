"""Materialization — turning Schedules into concrete Run rows over the horizon.

Cron occurrences are expanded in the Schedule's local wall-clock time, then
resolved to UTC Fire Times through Postgres so DST transitions are handled by
the same tz database the storage layer uses. Insertion is idempotent on
``(schedule_id, fire_time)``, so a re-run — or a leader handoff mid-sweep — never
double-creates an occurrence.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from croniter import croniter
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from thrum.jobs.models import Run, RunStatus, Schedule, Trigger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# How far to scan forward for a valid instant when a local time falls in a DST
# spring-forward gap. Bounds the search; no real gap approaches this.
_MAX_GAP_SCAN = "6 hours"

# For each local time, round-trip it through the zone: if it survives, it exists
# unambiguously and its UTC instant is taken directly. If it does not (a DST gap
# where that wall-clock time never occurs), scan forward minute by minute to the
# first instant that does exist.
_RESOLVE_FIRE_TIMES = text(
    f"""
    WITH locals AS (SELECT unnest(cast(:locals AS timestamp[])) AS l)
    SELECT
        l,
        CASE
            WHEN (l AT TIME ZONE :tz) AT TIME ZONE :tz = l
                THEN l AT TIME ZONE :tz
            ELSE (
                SELECT g AT TIME ZONE :tz
                FROM generate_series(l, l + interval '{_MAX_GAP_SCAN}', interval '1 minute') g
                WHERE (g AT TIME ZONE :tz) AT TIME ZONE :tz = g
                ORDER BY g
                LIMIT 1
            )
        END AS fire_time
    FROM locals
    ORDER BY l
    """
)


def expand_local(cron: str, lo: dt.datetime, hi: dt.datetime) -> list[dt.datetime]:
    """Enumerate a cron's occurrences in local wall-clock time within ``(lo, hi]``."""
    it = croniter(cron, lo)
    occurrences: list[dt.datetime] = []
    while True:
        nxt = it.get_next(dt.datetime)
        if nxt > hi:
            return occurrences
        occurrences.append(nxt)


async def resolve_fire_times(
    session: AsyncSession, tz: str, locals_: list[dt.datetime]
) -> list[dt.datetime]:
    """Resolve local occurrences to UTC Fire Times, folding DST gaps forward."""
    if not locals_:
        return []
    rows = (await session.execute(_RESOLVE_FIRE_TIMES, {"locals": locals_, "tz": tz})).all()
    return [row.fire_time for row in rows]


def _expectation(
    schedule: Schedule, fire_time: dt.datetime
) -> tuple[dt.datetime, dt.datetime | None, dt.timedelta | None]:
    """Derive the Expectation snapshot for one occurrence from the Schedule.

    The finish deadline prefers an explicit SLA over the declared duration.

    Returns:
        A tuple ``(expected_start_at, expected_finish_by, expected_duration)``,
        with the latter two ``None`` when the Schedule declares no timing.
    """
    expected_start_at = fire_time
    expected_duration = schedule.declared_duration
    if schedule.sla is not None:
        expected_finish_by = fire_time + schedule.sla
    elif schedule.declared_duration is not None:
        expected_finish_by = fire_time + schedule.declared_duration
    else:
        expected_finish_by = None
    return expected_start_at, expected_finish_by, expected_duration


async def materialize_schedules(session: AsyncSession, horizon: dt.timedelta) -> int:
    """Pre-create scheduled Runs for every active Schedule out to the horizon.

    Returns:
        The number of Run rows inserted; occurrences that already exist are
        skipped by the idempotent conflict clause.
    """
    db_now = (await session.execute(select(func.now()))).scalar_one()
    schedules = (
        (
            await session.execute(
                select(Schedule).where(
                    Schedule.declaration_active.is_(True),
                    Schedule.operationally_paused_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    inserted = 0
    for schedule in schedules:
        lo, hi = (
            await session.execute(
                text(
                    "SELECT (:now AT TIME ZONE :tz)::timestamp AS lo, "
                    "((:now + :horizon) AT TIME ZONE :tz)::timestamp AS hi"
                ),
                {"now": db_now, "tz": schedule.timezone, "horizon": horizon},
            )
        ).one()

        local_occurrences = expand_local(schedule.cron, lo, hi)
        fire_times = await resolve_fire_times(session, schedule.timezone, local_occurrences)
        if not fire_times:
            continue

        rows = []
        for fire_time in fire_times:
            expected_start_at, expected_finish_by, expected_duration = _expectation(
                schedule, fire_time
            )
            rows.append(
                {
                    "schedule_id": schedule.id,
                    "operation_namespace": schedule.operation_namespace,
                    "operation_name": schedule.operation_name,
                    "trigger": Trigger.schedule,
                    "status": RunStatus.scheduled,
                    "fire_time": fire_time,
                    "inputs": {},
                    "expected_start_at": expected_start_at,
                    "expected_finish_by": expected_finish_by,
                    "expected_duration": expected_duration,
                    "created_at": db_now,
                }
            )

        stmt = (
            pg_insert(Run)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_runs_schedule_fire_time")
        )
        result = await session.execute(stmt)
        inserted += result.rowcount

    return inserted
