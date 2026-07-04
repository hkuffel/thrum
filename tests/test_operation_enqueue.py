from __future__ import annotations

from sqlalchemy import select

from thrum import operation
from thrum.jobs.models import Run, RunStatus, Trigger


async def test_op_enqueue_writes_pending_run_with_renamed_columns(session_factory):
    @operation
    def send_receipts(invoice_id):
        return {"emailed": invoice_id}

    async with session_factory() as session:
        run = send_receipts.enqueue(session, invoice_id=42)
        await session.commit()
        run_id = run.id

    async with session_factory() as session:
        loaded = (await session.execute(select(Run).where(Run.id == run_id))).scalar_one()
        assert loaded.status == RunStatus.pending
        assert loaded.trigger == Trigger.enqueue
        assert loaded.operation_namespace == "default"
        assert loaded.operation_name == "send_receipts"
        assert loaded.inputs == {"invoice_id": 42}


async def test_op_enqueue_does_not_commit(session_factory):

    @operation
    def send_receipts(invoice_id):
        return {"emailed": invoice_id}

    async with session_factory() as session:
        run = send_receipts.enqueue(session, invoice_id=1)
        await session.rollback()
        rolled_back_id = run.id

    async with session_factory() as session:
        found = (
            await session.execute(select(Run).where(Run.id == rolled_back_id))
        ).scalar_one_or_none()
    assert found is None
