"""End-to-end integration test for the keystone tracer bullet:
author a bare `@operation` → call `op.enqueue(session, **inputs)` → assert a
`pending` Run row exists with the renamed `operation_namespace` / `operation_name`
columns, on the caller's session, without committing.

Runs against an ephemeral Postgres via the shared `session_factory` fixture and
skips when Docker is unavailable.
"""

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
        # Caller controls the commit — `op.enqueue` must not commit on its own.
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
    """Rollback after `op.enqueue` must wipe the Run — confirming the caller
    owns the transaction boundary (ADR-0001/0006)."""

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
