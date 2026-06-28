"""Lease-recovery tests: Heartbeat, leader election, and Reaper against real
Postgres, plus the Worker-death e2e.

Mirrors test_worker.py: assertions are on observable DB state (Attempt outcome /
ended_at / lease, Run status / next_attempt_at, advisory-lock holders), never on
internal wiring. The `session_factory` fixture gives a clean migrated schema.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select, update

from thrum.jobs import enqueue
from thrum.jobs.models import Attempt, AttemptOutcome, Run, RunStatus
from thrum.jobs.registry import Task
from thrum.jobs.scheduler.election import release_sweep_lock, try_acquire_sweep_lock
from thrum.jobs.scheduler.reaper import reap_orphans
from thrum.jobs.worker.claim import claim_runs
from thrum.jobs.worker.heartbeat import renew_leases

LEASE = dt.timedelta(seconds=45)


async def _enqueue(session_factory, key: str, **inputs):
    async with session_factory() as session:
        run = enqueue(session, key, **inputs)
        await session.commit()
        return run.id


async def _claim_one(session_factory, worker_id="worker-1"):
    async with session_factory() as session, session.begin():
        return (await claim_runs(session, worker_id, 10, LEASE))[0]


async def _force_lease_into_past(session_factory, attempt_id):
    """Simulate a dead Worker: its lease lapses because nothing renews it. We push
    `lease_expires_at` behind Postgres `now()` directly so the orphan window opens
    without waiting out a real TTL."""
    async with session_factory() as session, session.begin():
        await session.execute(
            update(Attempt)
            .where(Attempt.id == attempt_id)
            .values(lease_expires_at=func.now() - dt.timedelta(seconds=1))
        )


# Heartbeat (real DB)

async def test_heartbeat_renews_open_attempt(session_factory):
    await _enqueue(session_factory, "billing.send_receipts", invoice_id=1)
    item = await _claim_one(session_factory)
    await _force_lease_into_past(session_factory, item.attempt_id)

    async with session_factory() as session, session.begin():
        renewed = await renew_leases(session, "worker-1", LEASE)

    assert renewed == 1
    async with session_factory() as session:
        attempt = await session.get(Attempt, item.attempt_id)
        # DB-side: the lease now sits in the future again.
        db_now = (await session.execute(select(func.now()))).scalar_one()
        assert attempt.lease_expires_at > db_now
        assert attempt.ended_at is None


async def test_heartbeat_renews_only_this_workers_attempts(session_factory):
    await _enqueue(session_factory, "billing.send_receipts", invoice_id=1)
    mine = await _claim_one(session_factory, worker_id="worker-1")
    await _force_lease_into_past(session_factory, mine.attempt_id)

    await _enqueue(session_factory, "billing.send_receipts", invoice_id=2)
    theirs = await _claim_one(session_factory, worker_id="worker-2")
    await _force_lease_into_past(session_factory, theirs.attempt_id)

    async with session_factory() as session, session.begin():
        renewed = await renew_leases(session, "worker-1", LEASE)

    assert renewed == 1  # only worker-1's open Attempt
    async with session_factory() as session:
        other = await session.get(Attempt, theirs.attempt_id)
        db_now = (await session.execute(select(func.now()))).scalar_one()
        assert other.lease_expires_at < db_now  # untouched, still lapsed


async def test_heartbeat_does_not_renew_closed_attempt(session_factory):
    await _enqueue(session_factory, "billing.send_receipts", invoice_id=1)
    item = await _claim_one(session_factory)
    # Close the Attempt (as record would) and lapse its lease.
    async with session_factory() as session, session.begin():
        await session.execute(
            update(Attempt)
            .where(Attempt.id == item.attempt_id)
            .values(
                ended_at=func.now(),
                outcome=AttemptOutcome.succeeded,
                lease_expires_at=func.now() - dt.timedelta(seconds=1),
            )
        )

    async with session_factory() as session, session.begin():
        renewed = await renew_leases(session, "worker-1", LEASE)

    assert renewed == 0
    async with session_factory() as session:
        attempt = await session.get(Attempt, item.attempt_id)
        db_now = (await session.execute(select(func.now()))).scalar_one()
        assert attempt.lease_expires_at < db_now  # not renewed


# Leader election (real DB)

async def test_exactly_one_session_holds_the_sweep_lock(migrated_dsn, session_factory):
    from thrum.jobs.db.engine import make_async_engine

    # Two contending connections on a shared engine. Session-scoped advisory lock:
    # exactly one acquires; the other is warm failover until the first releases.
    engine = make_async_engine(migrated_dsn)
    c1 = await (await engine.connect()).execution_options(isolation_level="AUTOCOMMIT")
    c2 = await (await engine.connect()).execution_options(isolation_level="AUTOCOMMIT")
    try:
        assert await try_acquire_sweep_lock(c1) is True
        assert await try_acquire_sweep_lock(c2) is False  # warm failover

        assert await release_sweep_lock(c1) is True
        assert await try_acquire_sweep_lock(c2) is True  # second wins after release
    finally:
        await c1.close()
        await c2.close()
        await engine.dispose()


# Reaper (real DB)

async def test_reaper_closes_orphan_abandoned_and_requeues_run(session_factory):
    run_id = await _enqueue(session_factory, "billing.send_receipts", invoice_id=1)
    item = await _claim_one(session_factory)
    await _force_lease_into_past(session_factory, item.attempt_id)

    async with session_factory() as session, session.begin():
        reaped = await reap_orphans(session)

    assert reaped == 1
    async with session_factory() as session:
        attempt = await session.get(Attempt, item.attempt_id)
        run = await session.get(Run, run_id)
        assert attempt.outcome == AttemptOutcome.abandoned
        assert attempt.ended_at is not None
        # Unconditional requeue (ADR-0020): claimable again, no retry burned.
        assert run.status == RunStatus.pending
        assert run.next_attempt_at is not None


async def test_reaper_leaves_live_attempt_untouched(session_factory):
    run_id = await _enqueue(session_factory, "billing.send_receipts", invoice_id=1)
    item = await _claim_one(session_factory)  # fresh lease, 45s in the future

    async with session_factory() as session, session.begin():
        reaped = await reap_orphans(session)

    assert reaped == 0  # no false reap
    async with session_factory() as session:
        attempt = await session.get(Attempt, item.attempt_id)
        run = await session.get(Run, run_id)
        assert attempt.outcome is None
        assert attempt.ended_at is None
        assert run.status == RunStatus.running


async def test_reaped_run_is_immediately_reclaimable(session_factory):
    await _enqueue(session_factory, "billing.send_receipts", invoice_id=1)
    first = await _claim_one(session_factory)
    await _force_lease_into_past(session_factory, first.attempt_id)

    async with session_factory() as session, session.begin():
        await reap_orphans(session)

    # The requeued Run claims to a *fresh* Attempt (number 2 — no constraint clash).
    second = await _claim_one(session_factory, worker_id="worker-2")
    assert second.run_id == first.run_id
    assert second.attempt_id != first.attempt_id
    async with session_factory() as session:
        attempt = await session.get(Attempt, second.attempt_id)
        assert attempt.attempt_number == 2
        assert attempt.claimed_by == "worker-2"


# End-to-end: Worker death -> reaped -> re-runs

async def test_e2e_dead_worker_orphan_is_reaped_and_rerun(session_factory):
    def send(invoice_id):
        return {"emailed": invoice_id}

    task = Task(fn=send, namespace="billing", name="send_receipts")
    run_id = await _enqueue(session_factory, task.key, invoice_id=7)

    # Claim opens an Attempt + stamps a lease, then the Worker "dies": nothing
    # renews the lease, so we force it into the past.
    first = await _claim_one(session_factory, worker_id="worker-dead")
    await _force_lease_into_past(session_factory, first.attempt_id)

    # One sweep tick reaps the orphan.
    from thrum.jobs.config import SchedulerConfig
    from thrum.jobs.scheduler import Scheduler

    result = await Scheduler(SchedulerConfig(), session_factory).sweep()
    assert result.reaped == 1

    async with session_factory() as session:
        dead_attempt = await session.get(Attempt, first.attempt_id)
        run = await session.get(Run, run_id)
        assert dead_attempt.outcome == AttemptOutcome.abandoned
        assert run.status == RunStatus.pending

    # The next claim/execute/record pass on a live Worker runs it to success.
    from thrum.jobs.worker import run_once

    processed = await run_once(
        session_factory, "worker-live", 10, LEASE, operations={task.key: task}
    )
    assert processed == 1
    async with session_factory() as session:
        run = await session.get(Run, run_id)
        attempts = (
            (await session.execute(select(Attempt).where(Attempt.run_id == run_id)))
            .scalars()
            .all()
        )
        assert run.status == RunStatus.succeeded
        assert run.output == {"emailed": 7}
        outcomes = {a.attempt_number: a.outcome for a in attempts}
        assert outcomes == {1: AttemptOutcome.abandoned, 2: AttemptOutcome.succeeded}


async def test_e2e_reap_is_idempotent_within_a_sweep_pass(session_factory):
    """A second sweep with no new orphans reaps nothing — the requeued Run is
    `pending`, not an open lapsed Attempt, so it is not re-reaped."""
    await _enqueue(session_factory, "billing.send_receipts", invoice_id=1)
    item = await _claim_one(session_factory)
    await _force_lease_into_past(session_factory, item.attempt_id)

    async with session_factory() as session, session.begin():
        assert await reap_orphans(session) == 1
    async with session_factory() as session, session.begin():
        assert await reap_orphans(session) == 0
