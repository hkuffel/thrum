"""End-to-end test: the declared front door now feeds the wedge.

Declare an Operation with a due `schedule` → Worker startup assert writes the Schedule
row → the leader sweep materializes a `scheduled` Run → Claim flips it to
`running` → Record terminates it `succeeded`. No hand-inserted SQL — every row
along the path is written by the system, proving the declared front door drives
the materialization machinery end to end.

Skips gracefully without Docker via the `session_factory`/`migrated_dsn`
fixtures.
"""

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
    """The Registry's process-global view bleeds between tests; isolate it."""
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

    # Startup assert
    # The Worker's startup reconcile is what writes the `schedules` row. Drive
    # it directly (no `Worker.run` loop) so the test stays deterministic.
    worker = Worker(WorkerConfig(dsn=migrated_dsn))
    await worker._assert_schedules(session_factory)

    async with session_factory() as session:
        row = (await session.execute(select(Schedule))).scalars().one()
    assert row.operation_namespace == "e2e"
    assert row.operation_name == "send_receipts"
    assert row.cron == "* * * * *"
    assert row.declaration_active is True
    assert row.operationally_paused_at is None

    # Leader sweep materializes
    result = await Scheduler(SchedulerConfig(), session_factory).sweep()
    assert result.materialized > 0
    assert result.missed == 0

    # Make the earliest materialized occurrence due now so Claim can flip it
    # this pass (the test doesn't sleep for an actual minute).
    async with session_factory() as session, session.begin():
        earliest = (
            (await session.execute(select(Run).order_by(Run.fire_time).limit(1))).scalars().one()
        )
        earliest.fire_time = func.now() - dt.timedelta(seconds=1)
        target_id = earliest.id

    # Claim → execute → record
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
