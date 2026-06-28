"""Materialization + missed-detection tests: the cron wedge proven against real
Postgres.

Assertions are on observable DB state (Run status / fire_time / Expectation
columns, Attempt outcome) and the real timezone authority — Postgres `tzdata` via
`AT TIME ZONE`, never Python `zoneinfo` (ADR-0015). The `session_factory` fixture
gives a clean migrated schema.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select

from thrum.jobs.config import SchedulerConfig
from thrum.jobs.models import Attempt, AttemptOutcome, Run, RunStatus, Schedule, Trigger
from thrum.jobs.registry import Task
from thrum.jobs.scheduler import Scheduler
from thrum.jobs.scheduler.materialize import (
    expand_local,
    materialize_schedules,
    resolve_fire_times,
)
from thrum.jobs.scheduler.missed import mark_missed
from thrum.jobs.worker import run_once
from thrum.jobs.worker.claim import claim_runs

LEASE = dt.timedelta(seconds=45)
HORIZON = dt.timedelta(hours=24)


async def _add_schedule(session_factory, **kw) -> Schedule:
    defaults = dict(
        operation_namespace="billing",
        operation_name="send_receipts",
        cron="0 * * * *",  # hourly
        timezone="UTC",
        declaration_active=True,
    )
    defaults.update(kw)
    async with session_factory() as session, session.begin():
        sched = Schedule(**defaults)
        session.add(sched)
    async with session_factory() as session:
        return (
            await session.execute(select(Schedule).where(Schedule.cron == defaults["cron"]))
        ).scalars().first()


# Materialization

async def test_materialize_creates_scheduled_runs_over_horizon(session_factory):
    sched = await _add_schedule(session_factory, cron="0 * * * *", timezone="UTC")

    async with session_factory() as session, session.begin():
        inserted = await materialize_schedules(session, HORIZON)

    assert inserted > 0
    async with session_factory() as session:
        runs = (await session.execute(select(Run))).scalars().all()
        db_now = (await session.execute(select(func.now()))).scalar_one()
    assert len(runs) == inserted
    for run in runs:
        assert run.status == RunStatus.scheduled
        assert run.trigger == Trigger.schedule
        assert run.schedule_id == sched.id
        # Every occurrence is in the future and within the horizon.
        assert db_now < run.fire_time <= db_now + HORIZON
        # Hourly cron lands on the top of the hour (UTC).
        assert run.fire_time.minute == 0


async def test_materialize_snapshots_expectation(session_factory):
    await _add_schedule(
        session_factory,
        cron="30 2 * * *",  # one occurrence per day
        timezone="UTC",
        declared_duration=dt.timedelta(minutes=5),
        sla=dt.timedelta(minutes=10),
    )

    async with session_factory() as session, session.begin():
        await materialize_schedules(session, HORIZON)

    async with session_factory() as session:
        run = (await session.execute(select(Run))).scalars().first()
    assert run.expected_start_at == run.fire_time
    assert run.expected_duration == dt.timedelta(minutes=5)
    # SLA is the deadline (CONTEXT): expected_finish_by = fire_time + sla.
    assert run.expected_finish_by == run.fire_time + dt.timedelta(minutes=10)


async def test_materialize_is_idempotent(session_factory):
    await _add_schedule(session_factory, cron="0 * * * *", timezone="UTC")

    async with session_factory() as session, session.begin():
        first = await materialize_schedules(session, HORIZON)
    async with session_factory() as session, session.begin():
        second = await materialize_schedules(session, HORIZON)

    assert first > 0
    assert second == 0  # ON CONFLICT DO NOTHING — no double-creation
    async with session_factory() as session:
        total = (await session.execute(select(func.count()).select_from(Run))).scalar_one()
    assert total == first


async def test_declaration_gate_inactive_materializes_nothing(session_factory):
    await _add_schedule(
        session_factory, cron="0 * * * *", timezone="UTC", declaration_active=False
    )

    async with session_factory() as session, session.begin():
        inserted = await materialize_schedules(session, HORIZON)

    assert inserted == 0
    async with session_factory() as session:
        total = (await session.execute(select(func.count()).select_from(Run))).scalar_one()
    assert total == 0


async def test_operational_gate_paused_materializes_nothing(session_factory):
    await _add_schedule(
        session_factory,
        cron="0 * * * *",
        timezone="UTC",
        operation_name="op_paused_task",
        declaration_active=True,
        operationally_paused_at=dt.datetime.now(dt.UTC),
        operationally_paused_by="admin",
    )

    async with session_factory() as session, session.begin():
        inserted = await materialize_schedules(session, HORIZON)

    assert inserted == 0


async def test_both_gates_active_materializes(session_factory):
    await _add_schedule(
        session_factory,
        cron="0 * * * *",
        timezone="UTC",
        operation_name="both_active_task",
        declaration_active=True,
    )

    async with session_factory() as session, session.begin():
        inserted = await materialize_schedules(session, HORIZON)

    assert inserted > 0


# DST policy against real Postgres tzdata (ADR-0015)


async def test_dst_spring_forward_shifts_to_next_valid_instant(session_factory):
    """America/Vancouver springs forward 2026-03-08 02:00→03:00. A 02:30 local
    occurrence does not exist; it must shift to the next valid instant (03:00),
    so a daily job still runs that day (never silently skipped)."""
    gap_local = dt.datetime(2026, 3, 8, 2, 30)
    async with session_factory() as session:
        [fire_time] = await resolve_fire_times(session, "America/Vancouver", [gap_local])
        # Convert the resolved UTC instant back to Vancouver local wall-clock.
        back_to_local = (
            await session.execute(select(func.timezone("America/Vancouver", fire_time)))
        ).scalar_one()
    # The resolved instant's local wall-clock is 03:00 — the first valid instant.
    assert back_to_local == dt.datetime(2026, 3, 8, 3, 0)


async def test_dst_fall_back_fires_once(session_factory):
    """America/Vancouver falls back 2026-11-01 02:00→01:00, so 01:30 local occurs
    twice. A daily 01:30 job must resolve to a single instant — one fire_time, no
    double-fire (the cardinal sin)."""
    fold_local = dt.datetime(2026, 11, 1, 1, 30)
    # croniter emits the wall-clock once for the day...
    occurrences = expand_local(
        "30 1 * * *",
        dt.datetime(2026, 11, 1, 0, 0),
        dt.datetime(2026, 11, 1, 23, 59),
    )
    assert occurrences == [fold_local]
    # ...and resolution maps that single wall-clock to a single UTC instant.
    async with session_factory() as session:
        fire_times = await resolve_fire_times(session, "America/Vancouver", occurrences)
    assert len(fire_times) == 1


# `scheduled` claim arm

async def test_due_scheduled_run_is_claimed_and_flipped_running(session_factory):
    sched = await _add_schedule(session_factory)
    # A scheduled occurrence whose fire_time is already in the past.
    async with session_factory() as session, session.begin():
        run = Run(
            schedule_id=sched.id,
            operation_namespace="billing",
            operation_name="send_receipts",
            trigger=Trigger.schedule,
            status=RunStatus.scheduled,
            fire_time=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=5),
        )
        session.add(run)

    async with session_factory() as session, session.begin():
        claimed = await claim_runs(session, "worker-1", 10, LEASE)

    assert len(claimed) == 1
    async with session_factory() as session:
        got = await session.get(Run, claimed[0].run_id)
        assert got.status == RunStatus.running


async def test_future_scheduled_run_is_not_claimed(session_factory):
    sched = await _add_schedule(session_factory)
    async with session_factory() as session, session.begin():
        run = Run(
            schedule_id=sched.id,
            operation_namespace="billing",
            operation_name="send_receipts",
            trigger=Trigger.schedule,
            status=RunStatus.scheduled,
            fire_time=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        )
        session.add(run)
        await session.flush()  # populate the Python-side UUID default before reading id
        run_id = run.id

    async with session_factory() as session, session.begin():
        claimed = await claim_runs(session, "worker-1", 10, LEASE)

    assert claimed == []
    async with session_factory() as session:
        got = await session.get(Run, run_id)
        assert got.status == RunStatus.scheduled  # untouched


# Missed-detection

async def _add_scheduled_run(session_factory, *, fire_offset: dt.timedelta) -> Run:
    sched = await _add_schedule(session_factory)
    async with session_factory() as session, session.begin():
        run = Run(
            schedule_id=sched.id,
            operation_namespace="billing",
            operation_name="send_receipts",
            trigger=Trigger.schedule,
            status=RunStatus.scheduled,
            fire_time=func.now() + fire_offset,
        )
        session.add(run)
        await session.flush()
        return run.id


async def test_missed_marks_unclaimed_past_grace(session_factory):
    grace = dt.timedelta(seconds=30)
    run_id = await _add_scheduled_run(session_factory, fire_offset=-dt.timedelta(seconds=120))

    async with session_factory() as session, session.begin():
        marked = await mark_missed(session, grace)

    assert marked == 1
    async with session_factory() as session:
        run = await session.get(Run, run_id)
        assert run.status == RunStatus.missed


async def test_missed_respects_grace_floor_no_false_miss(session_factory):
    grace = dt.timedelta(seconds=60)
    # Only just past fire_time, well within the grace — must NOT be missed
    # (ordinary jitter: claimable-but-not-yet-claimed when the sweep fires).
    run_id = await _add_scheduled_run(session_factory, fire_offset=-dt.timedelta(seconds=5))

    async with session_factory() as session, session.begin():
        marked = await mark_missed(session, grace)

    assert marked == 0
    async with session_factory() as session:
        run = await session.get(Run, run_id)
        assert run.status == RunStatus.scheduled


async def test_missed_skips_already_claimed_run(session_factory):
    grace = dt.timedelta(seconds=30)
    run_id = await _add_scheduled_run(session_factory, fire_offset=-dt.timedelta(seconds=120))
    # Claim flips scheduled -> running before the grace elapses.
    async with session_factory() as session, session.begin():
        claimed = await claim_runs(session, "worker-1", 10, LEASE)
    assert [c.run_id for c in claimed] == [run_id]

    async with session_factory() as session, session.begin():
        marked = await mark_missed(session, grace)

    assert marked == 0  # `running` no longer matches the scheduled predicate
    async with session_factory() as session:
        run = await session.get(Run, run_id)
        assert run.status == RunStatus.running


# End-to-end: the whole wedge

async def test_e2e_schedule_materializes_claims_and_succeeds(session_factory):
    """Schedule → sweep materializes a due occurrence → Worker claims and runs it
    to `succeeded` — the cron wedge end to end against real Postgres."""

    def send():
        return {"sent": True}

    task = Task(fn=send, namespace="billing", name="send_receipts")
    # A cron that always has a due occurrence in the recent past: every minute.
    await _add_schedule(session_factory, cron="* * * * *", timezone="UTC")

    # Sweep materializes the horizon. Use a tiny grace so nothing is marked missed
    # spuriously; the first occurrence is in the future relative to now.
    result = await Scheduler(SchedulerConfig(), session_factory).sweep()
    assert result.materialized > 0
    assert result.missed == 0

    # Force the earliest occurrence due now so the Worker can claim it this pass.
    async with session_factory() as session, session.begin():
        earliest = (
            await session.execute(select(Run).order_by(Run.fire_time).limit(1))
        ).scalars().one()
        earliest.fire_time = func.now() - dt.timedelta(seconds=1)
        target_id = earliest.id

    processed = await run_once(
        session_factory, "worker-live", 10, LEASE, operations={task.key: task}
    )
    assert processed == 1
    async with session_factory() as session:
        run = await session.get(Run, target_id)
        attempt = (
            await session.execute(select(Attempt).where(Attempt.run_id == target_id))
        ).scalars().one()
        assert run.status == RunStatus.succeeded
        assert run.output == {"sent": True}
        assert attempt.outcome == AttemptOutcome.succeeded


async def test_e2e_unclaimed_occurrence_is_marked_missed(session_factory):
    """An occurrence whose fire_time passed by more than the grace, never claimed,
    becomes `missed` on a sweep — the observable terminal that defeats crontab
    blindness."""
    run_id = await _add_scheduled_run(session_factory, fire_offset=-dt.timedelta(minutes=5))

    # Default grace is 60s (floored at the 30s sweep interval); 5 min is well past.
    result = await Scheduler(SchedulerConfig(), session_factory).sweep()
    assert result.missed == 1
    async with session_factory() as session:
        run = await session.get(Run, run_id)
        assert run.status == RunStatus.missed
