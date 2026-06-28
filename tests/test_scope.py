"""Execution Scope tests (ADR-0024): the single-transaction injected-session
model against real Postgres.

The Scope owns Txn 2. A `scope_probe` table stands in for an Operation's own DB
effects so a test can assert what committed: on success the probe write and the
`succeeded` Attempt land together; on a raised body neither lands and the Attempt
is recorded `failed` in a separate transaction. Assertions are on observable DB
state, never internal wiring.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from thrum.jobs import App, CompileError, db_provider, enqueue, operation
from thrum.jobs.config import WorkerConfig
from thrum.jobs.models import Attempt, AttemptOutcome, Run, RunStatus, Schedule
from thrum.jobs.registry import Registry, Task
from thrum.jobs.scheduler.reaper import reap_orphans
from thrum.jobs.worker import Worker, run_once
from thrum.jobs.worker.claim import claim_runs
from thrum.jobs.worker.scope import run_scoped

LEASE = dt.timedelta(seconds=45)


class Telemetry:
    """A fake non-db Capability type, injected by `_recording_provider`."""


def _recording_provider(events: list[str]):
    """A Provider that records its enter/exit on `events` — proof the Scope tears
    every Provider down on the `AsyncExitStack`, success or raise."""

    @asynccontextmanager
    async def provider(ctx, caller):
        events.append("enter")
        try:
            yield Telemetry()
        finally:
            events.append("exit")

    return provider


async def _enqueue(session_factory, key: str, **inputs):
    async with session_factory() as session:
        run = enqueue(session, key, **inputs)
        await session.commit()
        return run.id


async def _claim_one(session_factory, worker_id="worker-1"):
    async with session_factory() as session, session.begin():
        return (await claim_runs(session, worker_id, 10, LEASE))[0]


async def _reset_probe(session_factory):
    async with session_factory() as session, session.begin():
        await session.execute(
            text("CREATE TABLE IF NOT EXISTS scope_probe (id serial PRIMARY KEY, note text)")
        )
        await session.execute(text("TRUNCATE scope_probe"))


async def _probe_count(session_factory) -> int:
    async with session_factory() as session:
        return (await session.execute(text("SELECT count(*) FROM scope_probe"))).scalar_one()


async def _write_probe(db: AsyncSession, note: str) -> None:
    await db.execute(text("INSERT INTO scope_probe (note) VALUES (:n)"), {"n": note})


# Success: the injected write and the completion record commit as one fact

async def test_injected_session_write_commits_with_attempt(session_factory):
    await _reset_probe(session_factory)

    async def writer(*, db: AsyncSession) -> dict:
        # The body never opens or imports a session — it uses the one injected.
        await _write_probe(db, "succeeded")
        return {"wrote": 1}

    task = Task(fn=writer, namespace="probe", name="writer")
    run_id = await _enqueue(session_factory, task.key)
    item = await _claim_one(session_factory)

    await run_scoped(session_factory, item, {task.key: task}, {AsyncSession: db_provider})

    assert await _probe_count(session_factory) == 1
    async with session_factory() as session:
        run = await session.get(Run, run_id)
        attempt = await session.get(Attempt, item.attempt_id)
    assert run.status == RunStatus.succeeded
    assert run.output == {"wrote": 1}
    assert attempt.outcome == AttemptOutcome.succeeded
    assert attempt.ended_at is not None


# Failure: Txn 2 rolls back; the Attempt is recorded in a separate transaction

async def test_raised_body_rolls_back_and_records_failed(session_factory):
    await _reset_probe(session_factory)

    async def boom(*, db: AsyncSession) -> dict:
        await _write_probe(db, "doomed")
        raise ValueError("nope")

    task = Task(fn=boom, namespace="probe", name="boom")
    run_id = await _enqueue(session_factory, task.key)
    item = await _claim_one(session_factory)

    await run_scoped(session_factory, item, {task.key: task}, {AsyncSession: db_provider})

    # Zero committed effects — the probe write rolled back with Txn 2.
    assert await _probe_count(session_factory) == 0
    async with session_factory() as session:
        run = await session.get(Run, run_id)
        attempt = await session.get(Attempt, item.attempt_id)
    assert run.status == RunStatus.failed  # never stuck `running`
    assert attempt.outcome == AttemptOutcome.failed
    assert "ValueError: nope" in attempt.error


async def test_non_serializable_output_fails_without_traceback(session_factory):
    async def bad_output(*, db: AsyncSession):
        return {"obj": object()}

    task = Task(fn=bad_output, namespace="probe", name="bad_output")
    run_id = await _enqueue(session_factory, task.key)
    item = await _claim_one(session_factory)

    await run_scoped(session_factory, item, {task.key: task}, {AsyncSession: db_provider})

    async with session_factory() as session:
        run = await session.get(Run, run_id)
        attempt = await session.get(Attempt, item.attempt_id)
    assert run.status == RunStatus.failed
    assert "not JSON-serializable" in attempt.error
    assert "Traceback" not in attempt.error


# Providers are entered and exited on the stack on both paths

async def test_providers_torn_down_on_success_path(session_factory):
    events: list[str] = []

    async def op(*, t: Telemetry) -> dict:
        return {}

    task = Task(fn=op, namespace="probe", name="ok")
    await _enqueue(session_factory, task.key)
    item = await _claim_one(session_factory)

    await run_scoped(
        session_factory, item, {task.key: task}, {Telemetry: _recording_provider(events)}
    )

    assert events == ["enter", "exit"]


async def test_providers_torn_down_on_failure_path(session_factory):
    events: list[str] = []

    async def op(*, t: Telemetry) -> dict:
        raise RuntimeError("boom")

    task = Task(fn=op, namespace="probe", name="raises")
    await _enqueue(session_factory, task.key)
    item = await _claim_one(session_factory)

    await run_scoped(
        session_factory, item, {task.key: task}, {Telemetry: _recording_provider(events)}
    )

    assert events == ["enter", "exit"]


# Crash before commit: a worker death mid-Txn-2 lands nothing; the Reaper recovers

async def test_crash_before_commit_lands_nothing_then_reruns(session_factory):
    await _reset_probe(session_factory)
    state = {"crash": True}

    async def flaky(*, db: AsyncSession) -> dict:
        await _write_probe(db, "attempt")
        if state["crash"]:
            raise asyncio.CancelledError  # worker death, not a Task failure
        return {"ok": True}

    task = Task(fn=flaky, namespace="probe", name="flaky")
    run_id = await _enqueue(session_factory, task.key)
    first = await _claim_one(session_factory, worker_id="worker-dead")

    with pytest.raises(asyncio.CancelledError):
        await run_scoped(session_factory, first, {task.key: task}, {AsyncSession: db_provider})

    # Nothing committed; the Run is still `running` with an open Attempt.
    assert await _probe_count(session_factory) == 0
    async with session_factory() as session:
        run = await session.get(Run, run_id)
        attempt = await session.get(Attempt, first.attempt_id)
    assert run.status == RunStatus.running
    assert attempt.ended_at is None

    # Lease lapses, the Reaper requeues, and re-execution succeeds (at-least-once).
    async with session_factory() as session, session.begin():
        await session.execute(
            update(Attempt)
            .where(Attempt.id == first.attempt_id)
            .values(lease_expires_at=func.now() - dt.timedelta(seconds=1))
        )
        await reap_orphans(session)

    state["crash"] = False
    second = await _claim_one(session_factory, worker_id="worker-live")
    await run_scoped(session_factory, second, {task.key: task}, {AsyncSession: db_provider})

    assert await _probe_count(session_factory) == 1
    async with session_factory() as session:
        run = await session.get(Run, run_id)
    assert run.status == RunStatus.succeeded


# Worker boot: compile fails fast, then schedules still reconcile

async def test_boot_fails_fast_on_unresolvable_capability(session_factory, migrated_dsn):
    reg = Registry("boot")

    @reg.operation
    async def needs_db(*, db: AsyncSession) -> dict: ...

    worker = Worker(WorkerConfig(dsn=migrated_dsn), App(registry=reg))  # no db provider
    with pytest.raises(CompileError, match="matches no registered capability"):
        await worker._boot(session_factory)


async def test_boot_reconciles_declared_schedules(session_factory, migrated_dsn):
    reg = Registry("boot")

    @reg.operation
    async def cronjob(*, db: AsyncSession) -> dict: ...

    cronjob.schedule("* * * * *", tz="UTC")
    app = App(registry=reg)
    app.provide(AsyncSession, db_provider)

    worker = Worker(WorkerConfig(dsn=migrated_dsn), app)
    await worker._boot(session_factory)

    async with session_factory() as session:
        row = (await session.execute(select(Schedule))).scalars().one()
    assert row.operation_name == "cronjob"
    assert row.declaration_active is True


# The Scope resolves operations + capabilities from the App, not Registry._global

async def test_run_once_with_app_injects_db_end_to_end(session_factory):
    await _reset_probe(session_factory)

    @operation
    async def writer(*, db: AsyncSession) -> dict:
        await _write_probe(db, "via-app")
        return {"ok": True}

    app = App()
    app.provide(AsyncSession, db_provider)
    run_id = await _enqueue(session_factory, "default.writer")

    processed = await run_once(session_factory, "worker-1", 10, LEASE, app=app)

    assert processed == 1
    assert await _probe_count(session_factory) == 1
    async with session_factory() as session:
        run = await session.get(Run, run_id)
    assert run.status == RunStatus.succeeded
    assert run.output == {"ok": True}


async def test_run_once_without_app_does_not_reach_global_registry(session_factory):
    # An Operation exists in the process-global registry, but run_once with no
    # App resolves nothing — the Registry._global default reach was retired.
    @operation
    async def ghost() -> dict:
        return {}

    run_id = await _enqueue(session_factory, "default.ghost")
    processed = await run_once(session_factory, "worker-1", 10, LEASE)

    assert processed == 1  # claimed, but unresolved → failed, not executed
    async with session_factory() as session:
        run = await session.get(Run, run_id)
        attempt = (
            await session.execute(select(Attempt).where(Attempt.run_id == run_id))
        ).scalars().one()
    assert run.status == RunStatus.failed
    assert "not registered" in attempt.error
