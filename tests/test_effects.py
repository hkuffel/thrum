from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from factories import make_operation
from thrum.jobs import db_provider, enqueue
from thrum.jobs.models import Attempt, AttemptOutcome, Effect, EffectKind, Run, RunStatus
from thrum.jobs.worker import effects as effects_module
from thrum.jobs.worker.claim import claim_runs
from thrum.jobs.worker.effects import is_zero_effect
from thrum.jobs.worker.scope import run_scoped

LEASE = dt.timedelta(seconds=45)
DB = {AsyncSession: db_provider}


async def _reset_probes(session_factory):
    async with session_factory() as session, session.begin():
        await session.execute(
            text("CREATE TABLE IF NOT EXISTS effect_probe (id serial PRIMARY KEY, note text)")
        )
        await session.execute(
            text("CREATE TABLE IF NOT EXISTS effect_probe_b (id serial PRIMARY KEY, note text)")
        )
        await session.execute(text("TRUNCATE effect_probe, effect_probe_b"))


async def _enqueue(session_factory, key: str, **inputs):
    async with session_factory() as session:
        run = enqueue(session, key, **inputs)
        await session.commit()
        return run.id


async def _claim_one(session_factory, worker_id="worker-1"):
    async with session_factory() as session, session.begin():
        return (await claim_runs(session, worker_id, 10, LEASE))[0]


async def _effects(session_factory, attempt_id) -> list[Effect]:
    async with session_factory() as session:
        return list(
            (
                await session.execute(
                    select(Effect)
                    .where(Effect.attempt_id == attempt_id)
                    .order_by(Effect.table_name)
                )
            ).scalars()
        )


async def test_batch_yields_one_effect_with_row_count(session_factory):
    await _reset_probes(session_factory)

    async def batch(*, db: AsyncSession) -> dict:
        await db.execute(
            text("INSERT INTO effect_probe (note) SELECT 'x' FROM generate_series(1, 100)")
        )
        return {}

    operation = make_operation(fn=batch, namespace="effect", name="batch")
    await _enqueue(session_factory, operation.key)
    item = await _claim_one(session_factory)
    await run_scoped(session_factory, item, {operation.key: operation}, DB)

    rows = await _effects(session_factory, item.attempt_id)
    assert len(rows) == 1
    assert rows[0].kind == EffectKind.insert
    assert rows[0].table_name == "effect_probe"
    assert rows[0].row_count == 100


async def test_kinds_and_multiple_tables(session_factory):
    await _reset_probes(session_factory)

    async def mutate(*, db: AsyncSession) -> dict:
        await db.execute(text("INSERT INTO effect_probe (note) VALUES ('a'), ('b')"))
        await db.execute(text("UPDATE effect_probe SET note = 'c'"))
        await db.execute(text("INSERT INTO effect_probe_b (note) VALUES ('z')"))
        await db.execute(text("DELETE FROM effect_probe_b"))
        return {}

    operation = make_operation(fn=mutate, namespace="effect", name="mutate")
    await _enqueue(session_factory, operation.key)
    item = await _claim_one(session_factory)
    await run_scoped(session_factory, item, {operation.key: operation}, DB)

    rows = await _effects(session_factory, item.attempt_id)
    recorded = {(r.table_name, r.kind): r.row_count for r in rows}
    assert recorded == {
        ("effect_probe", EffectKind.insert): 2,
        ("effect_probe", EffectKind.update): 2,
        ("effect_probe_b", EffectKind.insert): 1,
        ("effect_probe_b", EffectKind.delete): 1,
    }


async def test_dml_with_leading_comments_is_still_classified(session_factory):
    await _reset_probes(session_factory)

    async def commented(*, db: AsyncSession) -> dict:
        await db.execute(text("-- audit note\nINSERT INTO effect_probe (note) VALUES ('a')"))
        await db.execute(text("/* hint */ UPDATE effect_probe SET note = 'b'"))
        return {}

    operation = make_operation(fn=commented, namespace="effect", name="commented")
    await _enqueue(session_factory, operation.key)
    item = await _claim_one(session_factory)
    await run_scoped(session_factory, item, {operation.key: operation}, DB)

    rows = await _effects(session_factory, item.attempt_id)
    assert {(r.table_name, r.kind): r.row_count for r in rows} == {
        ("effect_probe", EffectKind.insert): 1,
        ("effect_probe", EffectKind.update): 1,
    }


async def test_success_commits_effects_records_and_attempt_atomically(session_factory):
    await _reset_probes(session_factory)

    async def writer(*, db: AsyncSession) -> dict:
        await db.execute(text("INSERT INTO effect_probe (note) VALUES ('one')"))
        return {"ok": True}

    operation = make_operation(fn=writer, namespace="effect", name="writer")
    run_id = await _enqueue(session_factory, operation.key)
    item = await _claim_one(session_factory)
    await run_scoped(session_factory, item, {operation.key: operation}, DB)

    async with session_factory() as session:
        probe = (await session.execute(text("SELECT count(*) FROM effect_probe"))).scalar_one()
        run = await session.get(Run, run_id)
        attempt = await session.get(Attempt, item.attempt_id)
    assert probe == 1
    assert run.status == RunStatus.succeeded
    assert attempt.outcome == AttemptOutcome.succeeded
    assert len(await _effects(session_factory, item.attempt_id)) == 1


async def test_recorder_listener_raise_does_not_roll_back_effects(session_factory, monkeypatch):
    await _reset_probes(session_factory)

    def boom(_clauseelement):
        raise RuntimeError("recorder boom")

    monkeypatch.setattr(effects_module, "_classify", boom)

    async def writer(*, db: AsyncSession) -> dict:
        await db.execute(text("INSERT INTO effect_probe (note) VALUES ('survives')"))
        return {"ok": True}

    operation = make_operation(fn=writer, namespace="effect", name="resilient")
    run_id = await _enqueue(session_factory, operation.key)
    item = await _claim_one(session_factory)
    await run_scoped(session_factory, item, {operation.key: operation}, DB)

    async with session_factory() as session:
        probe = (await session.execute(text("SELECT count(*) FROM effect_probe"))).scalar_one()
        run = await session.get(Run, run_id)
    assert probe == 1
    assert run.status == RunStatus.succeeded
    assert await _effects(session_factory, item.attempt_id) == []


async def test_failed_then_retried_attributes_effects_to_the_retry(session_factory):
    await _reset_probes(session_factory)
    state = {"fail": True}

    async def flaky(*, db: AsyncSession) -> dict:
        await db.execute(text("INSERT INTO effect_probe (note) VALUES ('try')"))
        if state["fail"]:
            raise ValueError("first try fails")
        return {"ok": True}

    operation = make_operation(fn=flaky, namespace="effect", name="flaky", max_attempts=2)
    run_id = await _enqueue(session_factory, operation.key)

    first = await _claim_one(session_factory)
    await run_scoped(session_factory, first, {operation.key: operation}, DB)

    async with session_factory() as session, session.begin():
        await session.execute(
            update(Run).where(Run.id == run_id).values(next_attempt_at=func.now())
        )
    state["fail"] = False
    second = await _claim_one(session_factory)
    await run_scoped(session_factory, second, {operation.key: operation}, DB)

    assert await _effects(session_factory, first.attempt_id) == []
    retry_effects = await _effects(session_factory, second.attempt_id)
    assert [(r.table_name, r.kind, r.row_count) for r in retry_effects] == [
        ("effect_probe", EffectKind.insert, 1)
    ]


async def test_zero_effect_flag_is_derived_and_leaves_status_unchanged(session_factory):
    await _reset_probes(session_factory)

    async def noop(*, db: AsyncSession) -> dict:
        return {"did": "nothing"}

    async def writer(*, db: AsyncSession) -> dict:
        await db.execute(text("INSERT INTO effect_probe (note) VALUES ('something')"))
        return {}

    noop_op = make_operation(fn=noop, namespace="effect", name="noop")
    writer_op = make_operation(fn=writer, namespace="effect", name="did_work")
    operations = {noop_op.key: noop_op, writer_op.key: writer_op}

    noop_run = await _enqueue(session_factory, noop_op.key)
    noop_item = await _claim_one(session_factory)
    await run_scoped(session_factory, noop_item, operations, DB)

    writer_run = await _enqueue(session_factory, writer_op.key)
    writer_item = await _claim_one(session_factory)
    await run_scoped(session_factory, writer_item, operations, DB)

    async with session_factory() as session:
        assert await is_zero_effect(session, noop_item.attempt_id) is True
        assert await is_zero_effect(session, writer_item.attempt_id) is False
        assert (await session.get(Run, noop_run)).status == RunStatus.succeeded
        assert (await session.get(Run, writer_run)).status == RunStatus.succeeded
