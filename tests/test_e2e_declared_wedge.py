from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import func, select

from thrum.jobs.config import SchedulerConfig, WorkerConfig
from thrum.jobs.models import Attempt, AttemptOutcome, Run, RunStatus, Schedule
from thrum.jobs.registry import Registry
from thrum.jobs.scheduler import Scheduler
from thrum.jobs.worker import Worker, run_once

LEASE = dt.timedelta(seconds=45)


@pytest.fixture(autouse=True)
def _clean_global_registry():
    saved_operations = Registry._global.copy()
    saved_schedules = Registry._global_schedules.copy()
    Registry._global.clear()
    Registry._global_schedules.clear()
    try:
        yield
    finally:
        Registry._global.clear()
        Registry._global.update(saved_operations)
        Registry._global_schedules.clear()
        Registry._global_schedules.update(saved_schedules)


async def test_e2e_declared_schedule_through_the_front_door(session_factory, migrated_dsn):
    reg = Registry("e2e")

    @reg.operation
    def send_receipts():
        return {"sent": True}

    send_receipts.schedule("* * * * *", tz="UTC")

    worker = Worker(WorkerConfig(dsn=migrated_dsn))
    await worker._assert_schedules(session_factory)

    async with session_factory() as session:
        row = (await session.execute(select(Schedule))).scalars().one()
    assert row.operation_namespace == "e2e"
    assert row.operation_name == "send_receipts"
    assert row.cron == "* * * * *"
    assert row.declaration_active is True
    assert row.operationally_paused_at is None

    result = await Scheduler(SchedulerConfig(), session_factory).sweep()
    assert result.materialized > 0
    assert result.missed == 0

    async with session_factory() as session, session.begin():
        earliest = (
            (await session.execute(select(Run).order_by(Run.fire_time).limit(1))).scalars().one()
        )
        earliest.fire_time = func.now() - dt.timedelta(seconds=1)
        target_id = earliest.id

    processed = await run_once(
        session_factory, "worker-e2e", 10, LEASE, operations=Registry._global
    )
    assert processed == 1

    async with session_factory() as session:
        run = await session.get(Run, target_id)
        attempt = (
            (await session.execute(select(Attempt).where(Attempt.run_id == target_id)))
            .scalars()
            .one()
        )
    assert run.status == RunStatus.succeeded
    assert run.output == {"sent": True}
    assert attempt.outcome == AttemptOutcome.succeeded
    assert attempt.ended_at is not None
