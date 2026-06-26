from __future__ import annotations

import asyncio
import datetime as dt

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from thrum.jobs.models import Schedule
from thrum.jobs.registry import DeclaredSchedule
from thrum.jobs.scheduler.reconcile import assert_declared_schedules


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


@pytest.mark.asyncio
async def test_new_schedule_inserts(session_factory):
    declared = {"billing.send_invoices": _spec(cron="0 9 * * 1")}

    async with session_factory() as s, s.begin():
        count = await assert_declared_schedules(s, declared)

    assert count == 1
    async with session_factory() as s:
        row = await _get_schedule(s, "billing", "send_invoices")
    assert row is not None
    assert row.cron == "0 9 * * 1"
    assert row.timezone == "UTC"
    assert row.declaration_active is True
    assert row.last_declared_at is not None
    assert row.operationally_paused_at is None


@pytest.mark.asyncio
async def test_idempotent_rerun_refreshes_last_declared_at(session_factory):
    declared = {"ns.job": _spec()}

    async with session_factory() as s, s.begin():
        await assert_declared_schedules(s, declared)
    async with session_factory() as s:
        first = await _get_schedule(s, "ns", "job")
    ts1 = first.last_declared_at

    await asyncio.sleep(0.05)
    async with session_factory() as s, s.begin():
        await assert_declared_schedules(s, declared)
    async with session_factory() as s:
        second = await _get_schedule(s, "ns", "job")
    assert second.id == first.id
    assert second.cron == first.cron
    assert second.last_declared_at >= ts1


@pytest.mark.asyncio
async def test_changed_cron_updates_in_place(session_factory):
    declared_v1 = {"ns.evolve": _spec(cron="0 2 * * *", timezone="America/Vancouver")}
    declared_v2 = {"ns.evolve": _spec(cron="30 3 * * *", timezone="Europe/London")}

    async with session_factory() as s, s.begin():
        await assert_declared_schedules(s, declared_v1)
    async with session_factory() as s:
        v1 = await _get_schedule(s, "ns", "evolve")

    async with session_factory() as s, s.begin():
        await assert_declared_schedules(s, declared_v2)
    async with session_factory() as s:
        v2 = await _get_schedule(s, "ns", "evolve")

    assert v2.id == v1.id
    assert v2.cron == "30 3 * * *"
    assert v2.timezone == "Europe/London"


@pytest.mark.asyncio
async def test_revival_reasserts_declaration_gate(session_factory):
    declared = {"ns.revive": _spec()}

    async with session_factory() as s, s.begin():
        await assert_declared_schedules(s, declared)

    # simulate gate-off (e.g. removed from code, then re-added)
    async with session_factory() as s, s.begin():
        row = await _get_schedule(s, "ns", "revive")
        row.declaration_active = False

    async with session_factory() as s, s.begin():
        await assert_declared_schedules(s, declared)

    async with session_factory() as s:
        row = await _get_schedule(s, "ns", "revive")
    assert row.declaration_active is True


@pytest.mark.asyncio
async def test_operational_pause_preserved_after_assert(session_factory):
    declared = {"ns.paused": _spec()}

    async with session_factory() as s, s.begin():
        await assert_declared_schedules(s, declared)

    # simulate v2 operational pause
    async with session_factory() as s, s.begin():
        row = await _get_schedule(s, "ns", "paused")
        row.operationally_paused_at = dt.datetime.now(dt.timezone.utc)
        row.operationally_paused_by = "ops-dashboard"

    async with session_factory() as s, s.begin():
        await assert_declared_schedules(s, declared)

    async with session_factory() as s:
        row = await _get_schedule(s, "ns", "paused")
    assert row.declaration_active is True
    assert row.operationally_paused_at is not None
    assert row.operationally_paused_by == "ops-dashboard"


@pytest.mark.asyncio
async def test_concurrent_asserts_converge(session_factory):
    declared = {"ns.concurrent": _spec()}

    async def do_assert():
        async with session_factory() as s, s.begin():
            await assert_declared_schedules(s, declared)

    await asyncio.gather(do_assert(), do_assert(), do_assert())

    async with session_factory() as s:
        rows = (
            await s.execute(
                select(Schedule).where(
                    Schedule.task_namespace == "ns",
                    Schedule.task_name == "concurrent",
                )
            )
        ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_empty_declared_set_is_noop(session_factory):
    async with session_factory() as s, s.begin():
        count = await assert_declared_schedules(s, {})
    assert count == 0


@pytest.mark.asyncio
async def test_policy_fields_persisted(session_factory):
    declared = {
        "ns.full": _spec(
            sla=dt.timedelta(minutes=30),
            declared_duration=dt.timedelta(minutes=5),
            start_grace=dt.timedelta(minutes=10),
        )
    }

    async with session_factory() as s, s.begin():
        await assert_declared_schedules(s, declared)

    async with session_factory() as s:
        row = await _get_schedule(s, "ns", "full")
    assert row.sla == dt.timedelta(minutes=30)
    assert row.declared_duration == dt.timedelta(minutes=5)
    assert row.start_grace == dt.timedelta(minutes=10)
