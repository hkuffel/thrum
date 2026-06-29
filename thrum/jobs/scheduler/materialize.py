"""Materialization (ADR-0003 / ADR-0015): the cron wedge.

Each leader sweep expands every active Schedule's cron over the Materialization
Horizon and pre-creates one `scheduled` Run per occurrence. Those Runs are
Postgres rows independent of the leader's liveness, so the next ~24h of work
survives scheduler downtime — and an occurrence that should have fired but didn't
is a row the sweep can mark `missed`, the crontab blindness Thrum sells against.

Cron expands on naive local wall-clock datetimes and Postgres resolves local→UTC
via `AT TIME ZONE`, so its bundled tzdata is the sole timezone authority and
`resolve_fire_times` owns the DST policy (ADR-0015). Inserts are ON CONFLICT DO
NOTHING on (schedule_id, fire_time), so idempotency lives in the unique
constraint, not in scheduler bookkeeping (ADR-0003).
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

# Maximum DST gap we will scan past when shifting a non-existent local time
# forward to the next valid instant. Real-world DST jumps are ≤ 1h; 6h is a safe
# ceiling that also covers exotic historical transitions without scanning the
# whole day.
_MAX_GAP_SCAN = "6 hours"

# Resolve a batch of naive local wall-clock datetimes to UTC `fire_time`s in one
# round-trip, applying the spring-forward shift DB-side (Postgres tzdata is the
# authority). The CASE detects a non-existent local time by round-tripping it
# through the zone: if `(l AT TIME ZONE tz) AT TIME ZONE tz <> l`, `l` fell in a
# spring-forward gap, so we scan minute-by-minute to the first valid instant.
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
    """Expand a cron expression into the naive local wall-clock occurrences in the
    half-open window `(lo, hi]`. Pure croniter field arithmetic — no timezone is
    involved here; the local→UTC conversion happens later, DB-side (ADR-0015)."""
    it = croniter(cron, lo)
    occurrences: list[dt.datetime] = []
    while True:
        nxt = it.get_next(dt.datetime)  # naive local, strictly after the cursor
        if nxt > hi:
            return occurrences
        occurrences.append(nxt)


async def resolve_fire_times(
    session: AsyncSession, tz: str, locals_: list[dt.datetime]
) -> list[dt.datetime]:
    """Resolve naive local wall-clock datetimes to UTC `fire_time`s via Postgres
    `AT TIME ZONE`, shifting spring-forward gap times to the next valid instant
    (ADR-0015). Order matches the input. Empty in → empty out."""
    if not locals_:
        return []
    rows = (await session.execute(_RESOLVE_FIRE_TIMES, {"locals": locals_, "tz": tz})).all()
    return [row.fire_time for row in rows]


def _expectation(
    schedule: Schedule, fire_time: dt.datetime
) -> tuple[dt.datetime, dt.datetime | None, dt.timedelta | None]:
    """Snapshot the Expectation for an occurrence from the Schedule's policy: when
    it should start, finish, and how long it should take. Captured immutably onto
    the Run so Late/Overrun stay derivable later from fixed expectations (they
    remain silent in v1). The SLA is the deadline (CONTEXT.md), so it drives
    `expected_finish_by`; absent an SLA, the declared duration does."""
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
    """Materialize every non-paused Schedule over `horizon`, idempotently. Returns
    the number of *newly* created `scheduled` Runs (occurrences already present are
    absorbed by ON CONFLICT). Must run inside an open transaction.

    The horizon window is computed from the Postgres clock: `(now(), now() +
    horizon]` in each Schedule's local wall-clock time, so cron expansion and the
    dispatch `now()` comparisons share one clock."""
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
        # Window bounds in the Schedule's local wall-clock time (Postgres resolves
        # the UTC→local conversion so tzdata is authoritative).
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
