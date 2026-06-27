from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from thrum.jobs.models import Schedule
from thrum.jobs.registry import DeclaredSchedule
from thrum.jobs.scheduler.reconcile import assert_declared_schedules, pause_stale_schedules


def _spec(**overrides):
    defaults = {"cron": "0 2 * * *", "timezone": "UTC"}
    defaults.update(overrides)
    return DeclaredSchedule(**defaults)


async def _get_schedule(session: AsyncSession, ns: str, name: str) -> Schedule | None:
    return (
        await session.execute(
            select(Schedule).where(
                Schedule.task_namespace == ns,
                Schedule.task_name == name,
            )
        )
    ).scalar_one_or_none()


async def _insert_and_age(session_factory, key: str, age: dt.timedelta):
    """Assert a schedule then backdate its last_declared_at by `age`."""
    ns, name = key.split(".", 1)
    async with session_factory() as s, s.begin():
        await assert_declared_schedules(s, {key: [_spec()]})
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "UPDATE thrum.schedules "
                "SET last_declared_at = now() - make_interval(secs => :secs) "
                "WHERE task_namespace = :ns AND task_name = :name"
            ),
            {"secs": age.total_seconds(), "ns": ns, "name": name},
        )


@pytest.mark.asyncio
async def test_stale_schedule_declaration_gate_cleared(session_factory):
    threshold = dt.timedelta(minutes=5)
    await _insert_and_age(session_factory, "ns.old_job", dt.timedelta(minutes=10))

    async with session_factory() as s, s.begin():
        paused = await pause_stale_schedules(s, threshold)

    assert paused == 1
    async with session_factory() as s:
        row = await _get_schedule(s, "ns", "old_job")
    assert row.declaration_active is False


@pytest.mark.asyncio
async def test_fresh_schedule_not_cleared(session_factory):
    threshold = dt.timedelta(minutes=5)
    declared = {"ns.fresh_job": [_spec()]}
    async with session_factory() as s, s.begin():
        await assert_declared_schedules(s, declared)

    async with session_factory() as s, s.begin():
        paused = await pause_stale_schedules(s, threshold)

    assert paused == 0
    async with session_factory() as s:
        row = await _get_schedule(s, "ns", "fresh_job")
    assert row.declaration_active is True


@pytest.mark.asyncio
async def test_already_gated_off_row_left_alone(session_factory):
    threshold = dt.timedelta(minutes=5)
    await _insert_and_age(session_factory, "ns.already_off", dt.timedelta(minutes=10))

    # gate it off first
    async with session_factory() as s, s.begin():
        await pause_stale_schedules(s, threshold)

    # second pass should find nothing to do
    async with session_factory() as s, s.begin():
        paused = await pause_stale_schedules(s, threshold)
    assert paused == 0


@pytest.mark.asyncio
async def test_deploy_anti_thrash(session_factory):
    """Assert {X,Y}, then repeatedly assert only {X} while Y stays within
    threshold — Y's gate is not cleared until it ages past the threshold."""
    threshold = dt.timedelta(minutes=5)
    both = {"ns.x": [_spec()], "ns.y": [_spec()]}
    only_x = {"ns.x": [_spec()]}

    # initial deploy declares both
    async with session_factory() as s, s.begin():
        await assert_declared_schedules(s, both)

    # new deploy only declares X — re-stamps X but not Y
    async with session_factory() as s, s.begin():
        await assert_declared_schedules(s, only_x)

    # Y is still within threshold — should not be paused
    async with session_factory() as s, s.begin():
        paused = await pause_stale_schedules(s, threshold)
    assert paused == 0

    async with session_factory() as s:
        y = await _get_schedule(s, "ns", "y")
    assert y.declaration_active is True

    # age Y past threshold
    async with session_factory() as s, s.begin():
        await s.execute(
            text(
                "UPDATE thrum.schedules "
                "SET last_declared_at = now() - make_interval(secs => :secs) "
                "WHERE task_namespace = 'ns' AND task_name = 'y'"
            ),
            {"secs": dt.timedelta(minutes=10).total_seconds()},
        )

    # now Y should be paused, X should not
    async with session_factory() as s, s.begin():
        paused = await pause_stale_schedules(s, threshold)
    assert paused == 1

    async with session_factory() as s:
        x = await _get_schedule(s, "ns", "x")
        y = await _get_schedule(s, "ns", "y")
    assert x.declaration_active is True
    assert y.declaration_active is False


@pytest.mark.asyncio
async def test_operational_pause_not_touched(session_factory):
    """An operationally-paused row stays paused regardless of stale sweep."""
    threshold = dt.timedelta(minutes=5)
    await _insert_and_age(session_factory, "ns.ops_paused", dt.timedelta(minutes=10))

    # simulate v2 operational pause
    async with session_factory() as s, s.begin():
        row = await _get_schedule(s, "ns", "ops_paused")
        row.operationally_paused_at = dt.datetime.now(dt.UTC)
        row.operationally_paused_by = "admin"

    async with session_factory() as s, s.begin():
        await pause_stale_schedules(s, threshold)

    async with session_factory() as s:
        row = await _get_schedule(s, "ns", "ops_paused")
    assert row.operationally_paused_at is not None
    assert row.operationally_paused_by == "admin"


@pytest.mark.asyncio
async def test_pause_stale_runs_in_sweep(session_factory):
    """pause_stale_schedules is called inside Scheduler.sweep."""
    from thrum.jobs.config import SchedulerConfig
    from thrum.jobs.scheduler import Scheduler

    threshold = dt.timedelta(minutes=5)
    config = SchedulerConfig(stale_threshold=threshold)
    scheduler = Scheduler(config, session_factory)

    await _insert_and_age(session_factory, "ns.swept", dt.timedelta(minutes=10))

    result = await scheduler.sweep()
    assert result.stale_paused == 1

    async with session_factory() as s:
        row = await _get_schedule(s, "ns", "swept")
    assert row.declaration_active is False
