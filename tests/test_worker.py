from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from factories import make_operation
from thrum.jobs import enqueue
from thrum.jobs.models import Attempt, AttemptOutcome, Run, RunStatus
from thrum.jobs.worker import run_once
from thrum.jobs.worker.claim import claim_runs
from thrum.jobs.worker.execute import ExecutionResult
from thrum.jobs.worker.record import record_result

LEASE = dt.timedelta(seconds=45)


async def _enqueue(session_factory, key: str, **inputs):
    async with session_factory() as session:
        run = enqueue(session, key, **inputs)
        await session.commit()
        return run.id


async def test_claim_marks_running_and_stamps_lease(session_factory):
    run_id = await _enqueue(session_factory, "billing.send_receipts", invoice_id=1)

    async with session_factory() as session, session.begin():
        claimed = await claim_runs(session, "worker-1", 10, LEASE)

    assert len(claimed) == 1
    item = claimed[0]
    assert item.run_id == run_id
    assert item.operation_key == "billing.send_receipts"
    assert item.inputs == {"invoice_id": 1}

    async with session_factory() as session:
        run = await session.get(Run, run_id)
        attempt = await session.get(Attempt, item.attempt_id)
        assert run.status == RunStatus.running
        assert attempt.attempt_number == 1
        assert attempt.claimed_by == "worker-1"
        assert attempt.lease_expires_at > attempt.started_at
        assert attempt.ended_at is None


async def test_claim_skip_locked_prevents_double_claim(session_factory):
    await _enqueue(session_factory, "billing.send_receipts", invoice_id=1)

    s1 = session_factory()
    s2 = session_factory()
    try:
        claimed1 = await claim_runs(s1, "worker-1", 10, LEASE)
        claimed2 = await claim_runs(s2, "worker-2", 10, LEASE)
        assert len(claimed1) == 1
        assert len(claimed2) == 0
        await s1.commit()
    finally:
        await s2.rollback()
        await s1.close()
        await s2.close()


async def test_claim_respects_capacity_limit(session_factory):
    for i in range(3):
        await _enqueue(session_factory, "billing.send_receipts", invoice_id=i)

    async with session_factory() as session, session.begin():
        claimed = await claim_runs(session, "worker-1", 2, LEASE)

    assert len(claimed) == 2


async def test_record_success_sets_run_terminal(session_factory):
    run_id = await _enqueue(session_factory, "billing.send_receipts", invoice_id=1)
    async with session_factory() as session, session.begin():
        item = (await claim_runs(session, "worker-1", 10, LEASE))[0]

    async with session_factory() as session, session.begin():
        await record_result(
            session, item, ExecutionResult(AttemptOutcome.succeeded, {"emailed": 1}, None)
        )

    async with session_factory() as session:
        run = await session.get(Run, run_id)
        attempt = await session.get(Attempt, item.attempt_id)
        assert run.status == RunStatus.succeeded
        assert run.output == {"emailed": 1}
        assert attempt.outcome == AttemptOutcome.succeeded
        assert attempt.ended_at is not None


async def test_record_failure_sets_run_failed(session_factory):
    run_id = await _enqueue(session_factory, "billing.broken")
    async with session_factory() as session, session.begin():
        item = (await claim_runs(session, "worker-1", 10, LEASE))[0]

    async with session_factory() as session, session.begin():
        await record_result(
            session, item, ExecutionResult(AttemptOutcome.failed, None, "Traceback…")
        )

    async with session_factory() as session:
        run = await session.get(Run, run_id)
        attempt = await session.get(Attempt, item.attempt_id)
        assert run.status == RunStatus.failed
        assert attempt.outcome == AttemptOutcome.failed
        assert attempt.error == "Traceback…"


async def test_e2e_enqueue_runs_to_succeeded(session_factory):
    def send(invoice_id):
        return {"emailed": invoice_id}

    operation = make_operation(fn=send, namespace="billing", name="send_receipts")
    run_id = await _enqueue(session_factory, operation.key, invoice_id=7)

    processed = await run_once(
        session_factory, "worker-1", 10, LEASE, operations={operation.key: operation}
    )

    assert processed == 1
    async with session_factory() as session:
        run = await session.get(Run, run_id)
        attempt = (
            await session.execute(select(Attempt).where(Attempt.run_id == run_id))
        ).scalar_one()
        assert run.status == RunStatus.succeeded
        assert run.output == {"emailed": 7}
        assert attempt.outcome == AttemptOutcome.succeeded


async def test_e2e_raising_operation_records_failed(session_factory):
    def boom():
        raise ValueError("nope")

    operation = make_operation(fn=boom, namespace="billing", name="broken")
    run_id = await _enqueue(session_factory, operation.key)

    processed = await run_once(
        session_factory, "worker-1", 10, LEASE, operations={operation.key: operation}
    )

    assert processed == 1
    async with session_factory() as session:
        run = await session.get(Run, run_id)
        attempt = (
            await session.execute(select(Attempt).where(Attempt.run_id == run_id))
        ).scalar_one()
        assert run.status == RunStatus.failed
        assert attempt.outcome == AttemptOutcome.failed
        assert "ValueError" in attempt.error


async def test_e2e_no_work_processes_nothing(session_factory):
    processed = await run_once(session_factory, "worker-1", 10, LEASE, operations={})
    assert processed == 0
