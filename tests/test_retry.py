"""Retry + backoff tests (PRD 0004).

The backoff curve is pure (no DB, no clock) and tested directly here with a fixed
rng so jitter is deterministic. The budget decision is proven against real
Postgres in test_worker.py / the e2e below: assertions are on observable DB state
(Run status / next_attempt_at, Attempt outcomes / numbers), never internal wiring.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select

from thrum.jobs import enqueue
from thrum.jobs.models import Attempt, AttemptOutcome, Run, RunStatus
from thrum.jobs.registry import Task
from thrum.jobs.retry import RetryPolicy, backoff_delay
from thrum.jobs.worker import run_once
from thrum.jobs.worker.claim import claim_runs
from thrum.jobs.worker.execute import ExecutionResult
from thrum.jobs.worker.record import record_result

LEASE = dt.timedelta(seconds=45)


def _seconds(td: dt.timedelta) -> float:
    return td.total_seconds()


async def _enqueue(session_factory, key: str, **inputs):
    async with session_factory() as session:
        run = enqueue(session, key, **inputs)
        await session.commit()
        return run.id


async def _claim_one(session_factory, worker_id="worker-1"):
    async with session_factory() as session, session.begin():
        return (await claim_runs(session, worker_id, 10, LEASE))[0]


async def _fail(session_factory, item, task):
    """Record a Task-attributable failure for `item` under `task`'s policy."""
    async with session_factory() as session, session.begin():
        await record_result(
            session, item, ExecutionResult(AttemptOutcome.failed, None, "boom"), task
        )


# --- The pure curve (no DB) ------------------------------------------------


def test_backoff_no_jitter_is_capped_exponential():
    policy = RetryPolicy(initial_delay=1.0, backoff_factor=2.0, max_delay=300.0, jitter=False)
    # 1 → 2 → 4 → 8 ... then capped at 300.
    assert _seconds(backoff_delay(1, policy)) == 1.0
    assert _seconds(backoff_delay(2, policy)) == 2.0
    assert _seconds(backoff_delay(3, policy)) == 4.0
    assert _seconds(backoff_delay(4, policy)) == 8.0
    # Far enough out that the exponential exceeds the cap.
    assert _seconds(backoff_delay(20, policy)) == 300.0


def test_backoff_full_jitter_stays_within_ceiling():
    policy = RetryPolicy(initial_delay=1.0, backoff_factor=2.0, max_delay=300.0, jitter=True)
    # rng() -> 1.0 yields the ceiling; -> 0.0 yields zero; -> 0.5 the midpoint.
    assert _seconds(backoff_delay(3, policy, rng=lambda: 1.0)) == 4.0
    assert _seconds(backoff_delay(3, policy, rng=lambda: 0.0)) == 0.0
    assert _seconds(backoff_delay(3, policy, rng=lambda: 0.5)) == 2.0


def test_backoff_jitter_real_rng_never_exceeds_ceiling():
    policy = RetryPolicy(initial_delay=1.0, backoff_factor=2.0, max_delay=300.0, jitter=True)
    for _ in range(1000):
        assert 0.0 <= _seconds(backoff_delay(5, policy)) <= 16.0  # ceiling = 1*2^4


def test_backoff_failure_index_clamped_at_one():
    policy = RetryPolicy(initial_delay=1.0, backoff_factor=2.0, jitter=False)
    # 0 / negative degrade to the first-step delay, not a negative exponent.
    assert _seconds(backoff_delay(0, policy)) == 1.0
    assert _seconds(backoff_delay(-3, policy)) == 1.0


def test_retry_policy_defaults_are_the_beachhead():
    p = RetryPolicy()
    assert p.max_attempts == 1
    assert (p.initial_delay, p.max_delay, p.backoff_factor, p.jitter) == (1.0, 300.0, 2.0, True)


# --- The budget decision in Record (real DB) -------------------------------


async def test_failure_with_budget_remaining_requeues_with_backoff(session_factory):
    """A failing Task with budget left returns to `pending` with a future
    next_attempt_at — the Run loops (ADR-0013) rather than failing terminally."""
    task = Task(fn=lambda: None, namespace="billing", name="send_receipts", max_attempts=3)
    run_id = await _enqueue(session_factory, task.key)

    item = await _claim_one(session_factory)
    await _fail(session_factory, item, task)

    async with session_factory() as session:
        run = await session.get(Run, run_id)
        now = (await session.execute(select(func.now()))).scalar_one()
        assert run.status == RunStatus.pending
        assert run.next_attempt_at is not None
        # default 1s initial delay (jitter may pull it toward 0, but it is set);
        # it is at/after the recorded instant, gating immediate re-claim.
        assert run.next_attempt_at >= run.created_at
        assert run.next_attempt_at <= now + dt.timedelta(seconds=2)


async def test_budget_exhaustion_sets_run_failed(session_factory):
    """The Run goes terminal `failed` only once max_attempts Task-attributable
    Attempts are spent — not before."""
    # Zero backoff so the retry is immediately re-claimable (the backoff envelope
    # is covered by the pure tests + test_failure_with_budget_remaining_*).
    task = Task(
        fn=lambda: None,
        namespace="billing",
        name="send_receipts",
        max_attempts=2,
        retry_initial_delay=0.0,
        retry_jitter=False,
    )
    run_id = await _enqueue(session_factory, task.key)

    item1 = await _claim_one(session_factory)
    await _fail(session_factory, item1, task)
    async with session_factory() as session:  # still retrying after 1/2
        assert (await session.get(Run, run_id)).status == RunStatus.pending

    item2 = await _claim_one(session_factory)
    await _fail(session_factory, item2, task)
    async with session_factory() as session:  # budget spent after 2/2
        run = await session.get(Run, run_id)
        assert run.status == RunStatus.failed
        count = (
            await session.execute(
                select(func.count()).select_from(Attempt).where(Attempt.run_id == run_id)
            )
        ).scalar_one()
        assert count == 2


async def test_default_single_attempt_is_terminal_on_first_failure(session_factory):
    """max_attempts=1 (the default) keeps the old behavior: one raise → failed."""
    task = Task(fn=lambda: None, namespace="billing", name="send_receipts")
    run_id = await _enqueue(session_factory, task.key)

    item = await _claim_one(session_factory)
    await _fail(session_factory, item, task)

    async with session_factory() as session:
        run = await session.get(Run, run_id)
        assert run.status == RunStatus.failed
        assert run.next_attempt_at is None


async def test_abandoned_attempt_does_not_spend_budget(session_factory):
    """ADR-0020: a reaped (`abandoned`) Attempt — a Worker death, not a Task
    failure — must not count toward the budget, so a max_attempts=2 Run with one
    abandoned + one failed Attempt is still retried, not failed."""
    task = Task(
        fn=lambda: None,
        namespace="billing",
        name="send_receipts",
        max_attempts=2,
        retry_initial_delay=0.0,
        retry_jitter=False,
    )
    run_id = await _enqueue(session_factory, task.key)

    # First claim is "killed": close its Attempt `abandoned` and requeue, exactly
    # as the Reaper would (no budget consumed).
    item1 = await _claim_one(session_factory)
    async with session_factory() as session, session.begin():
        attempt = await session.get(Attempt, item1.attempt_id)
        attempt.ended_at = (await session.execute(select(func.now()))).scalar_one()
        attempt.outcome = AttemptOutcome.abandoned
        run = await session.get(Run, run_id)
        run.status = RunStatus.pending

    # Now a genuine Task failure (the first one that counts) under max_attempts=2.
    item2 = await _claim_one(session_factory)
    await _fail(session_factory, item2, task)

    async with session_factory() as session:
        run = await session.get(Run, run_id)
        # If `abandoned` had counted, budget would be spent (2) → failed. It does
        # not, so only the single `failed` counts (1/2) → still retrying.
        assert run.status == RunStatus.pending


async def test_unresolved_task_is_terminal_not_retried(session_factory):
    """An unresolved Task (None policy) is non-retryable: terminal `failed`,
    surfacing the misconfiguration instead of hot-looping (ADR-0021)."""
    run_id = await _enqueue(session_factory, "ghost.task")
    item = await _claim_one(session_factory)

    async with session_factory() as session, session.begin():
        await record_result(
            session, item, ExecutionResult(AttemptOutcome.failed, None, "boom"), None
        )

    async with session_factory() as session:
        run = await session.get(Run, run_id)
        assert run.status == RunStatus.failed
        assert run.next_attempt_at is None


# --- End-to-end through run_once (real DB) ---------------------------------


def _flaky_task(fails: int) -> Task:
    """A Task that raises its first `fails` invocations, then returns. Backoff is
    zeroed (initial_delay=0, jitter off) so each retry is immediately claimable,
    keeping the e2e fast and deterministic."""
    state = {"n": 0}

    def fn():
        state["n"] += 1
        if state["n"] <= fails:
            raise ValueError("transient")
        return {"ok": state["n"]}

    return Task(
        fn=fn,
        namespace="billing",
        name="flaky",
        max_attempts=5,
        retry_initial_delay=0.0,
        retry_jitter=False,
    )


async def test_e2e_task_succeeds_after_two_retries(session_factory):
    task = _flaky_task(fails=2)
    run_id = await _enqueue(session_factory, task.key)
    tasks = {task.key: task}

    for _ in range(3):  # fail, fail, succeed
        await run_once(session_factory, "worker-1", 10, LEASE, tasks=tasks)

    async with session_factory() as session:
        run = await session.get(Run, run_id)
        attempts = (
            await session.execute(
                select(Attempt).where(Attempt.run_id == run_id).order_by(Attempt.attempt_number)
            )
        ).scalars().all()

    assert run.status == RunStatus.succeeded
    assert run.output == {"ok": 3}
    assert [a.attempt_number for a in attempts] == [1, 2, 3]
    assert [a.outcome for a in attempts] == [
        AttemptOutcome.failed,
        AttemptOutcome.failed,
        AttemptOutcome.succeeded,
    ]


async def test_e2e_always_failing_task_exhausts_budget(session_factory):
    def boom():
        raise ValueError("always")

    task = Task(
        fn=boom,
        namespace="billing",
        name="doomed",
        max_attempts=2,
        retry_initial_delay=0.0,
        retry_jitter=False,
    )
    run_id = await _enqueue(session_factory, task.key)
    tasks = {task.key: task}

    await run_once(session_factory, "worker-1", 10, LEASE, tasks=tasks)
    await run_once(session_factory, "worker-1", 10, LEASE, tasks=tasks)
    # Budget spent — nothing left to claim.
    processed = await run_once(session_factory, "worker-1", 10, LEASE, tasks=tasks)

    assert processed == 0
    async with session_factory() as session:
        run = await session.get(Run, run_id)
        count = (
            await session.execute(
                select(func.count()).select_from(Attempt).where(Attempt.run_id == run_id)
            )
        ).scalar_one()
    assert run.status == RunStatus.failed
    assert count == 2
