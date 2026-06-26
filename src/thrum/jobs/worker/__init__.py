"""The Worker (CONTEXT): a standalone process that loads the user's Task code,
claims due Runs from Postgres, and executes them out-of-process from the app
(ADR-0001). One asyncio event loop (ADR-0005).

This slice (0002) adds the lease-recovery substrate on top of 0001's enqueue →
claim → execute → record path:

  - the **Heartbeat** — one per-Worker coroutine renewing every open Attempt's
    lease on a dedicated connection (ADR-0013);
  - **leader election** — each Worker contends for a session-scoped advisory lock
    on its own dedicated connection (ADR-0007);
  - the **leader-gated sweep** — the winner reaps orphaned Attempts (ADR-0013/0020).

As of 0003 the leader-gated sweep also materializes the horizon and marks missed
occurrences, and Claim handles the `scheduled` arm — so cron work flows end to end.

As of 0004 a Task-attributable failure no longer fails terminally on the first
raise: Record retries the Run (back to `pending` with a backed-off
`next_attempt_at`) until its Attempt budget is spent (ADR-0020/0021).

Still deliberately not here (each a later slice): thread/process-pool execution
contexts (and hardening the heartbeat against loop-starvation), graceful drain
(`requeued`), and lineage.
"""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from typing import TYPE_CHECKING

from thrum.jobs.config import SchedulerConfig, WorkerConfig
from thrum.jobs.registry import Registry
from thrum.jobs.worker.claim import claim_runs
from thrum.jobs.worker.execute import execute_run
from thrum.jobs.worker.heartbeat import renew_leases
from thrum.jobs.worker.record import record_result

if TYPE_CHECKING:
    import datetime as dt

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from thrum.jobs.registry import Task


async def run_once(
    session_factory: async_sessionmaker,
    worker_id: str,
    limit: int,
    lease_ttl: dt.timedelta,
    tasks: dict[str, Task] | None = None,
) -> int:
    """One claim → execute → record pass. Returns the number of Runs processed.

    Claim commits (releasing row locks) before any Task executes; each Run's
    result is then recorded in its own transaction. The testable seam beneath
    `Worker.run`.

    PORT SEAM (deferred): this three-transaction shape (claim | execute | record)
    is where thrum's single-transaction, injected-session execution lands — see
    the note in `worker/execute.py`. The future model collapses execute + effect-
    recording + record into one transaction with a framework-built session
    capability. Left as-is by the port.
    """
    if tasks is None:
        tasks = Registry._global

    async with session_factory() as session:
        async with session.begin():
            claimed = await claim_runs(session, worker_id, limit, lease_ttl)

    for item in claimed:
        result = await execute_run(item, tasks)
        async with session_factory() as session:
            async with session.begin():
                # Record needs the Task's retry policy to decide retry-vs-terminal
                # (ADR-0021); an unresolved Task (None) is non-retryable.
                await record_result(session, item, result, tasks.get(item.task_key))

    return len(claimed)


class Worker:
    def __init__(self, config: WorkerConfig) -> None:
        self.config = config
        self.id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._draining = False
        self._stop: asyncio.Event | None = None

    async def run(self) -> None:
        """The Worker loop: claim → execute → record → interruptible sleep
        (polling is the source of truth, ADR-0008). Alongside it run two
        background loops for this Worker's whole life — the Heartbeat (every
        Worker) and the leader-gated sweep (whichever Worker holds the lock).

        Each loop gets its own engine so the three concerns never share a pooled
        connection (ADR-0013): a Task holding an execution session open must not
        be able to block lease renewal, and the advisory lock must live on a
        connection nothing else touches."""
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from thrum.jobs.db.engine import make_async_engine

        self._stop = asyncio.Event()
        if self._draining:  # drain requested before we started up
            self._stop.set()

        exec_engine = make_async_engine(self.config.dsn)
        hb_engine = make_async_engine(self.config.dsn)
        leader_engine = make_async_engine(self.config.dsn)
        exec_factory = async_sessionmaker(exec_engine, expire_on_commit=False)
        hb_factory = async_sessionmaker(hb_engine, expire_on_commit=False)
        sweep_factory = async_sessionmaker(exec_engine, expire_on_commit=False)

        await self._assert_schedules(exec_factory)

        heartbeat = asyncio.create_task(self._heartbeat_loop(hb_factory))
        sweeper = asyncio.create_task(self._sweep_loop(leader_engine, sweep_factory))
        try:
            while not self._draining:
                processed = await run_once(
                    exec_factory,
                    self.id,
                    self.config.max_in_flight,
                    self.config.lease_ttl,
                )
                if processed == 0:
                    await self._sleep(self.config.poll_interval)
        finally:
            self._stop.set()
            await asyncio.gather(heartbeat, sweeper, return_exceptions=True)
            await exec_engine.dispose()
            await hb_engine.dispose()
            await leader_engine.dispose()

    async def _assert_schedules(self, session_factory: async_sessionmaker) -> None:
        from thrum.jobs.scheduler.reconcile import assert_declared_schedules

        declared = Registry._global_schedules
        if not declared:
            return
        async with session_factory() as session, session.begin():
            await assert_declared_schedules(session, declared)

    async def _heartbeat_loop(self, session_factory: async_sessionmaker) -> None:
        """Renew this Worker's open-Attempt leases every `heartbeat_interval`
        (config; ≤ lease_ttl/3 so two missed ticks are needed to look orphaned)
        until drain. A failed tick is swallowed so a transient DB blip cannot kill
        the heartbeat — the next tick recovers well within the lease window."""
        assert self._stop is not None
        while not self._draining:
            try:
                async with session_factory() as session, session.begin():
                    await renew_leases(session, self.id, self.config.lease_ttl)
            except Exception:  # pragma: no cover - best-effort liveness loop
                pass
            await self._sleep(self.config.heartbeat_interval)

    async def _sweep_loop(self, leader_engine, session_factory: async_sessionmaker) -> None:
        """Contend for the sweep lock each tick; while held, run the reconciliation
        sweep (materialize + mark missed + reap). The advisory-lock connection is held open for the
        whole loop, so on this Worker's death Postgres releases the lock and the
        next Worker wins (ADR-0007). AUTOCOMMIT keeps that connection out of an
        idle-in-transaction state between `try`s."""
        from thrum.jobs.scheduler import Scheduler
        from thrum.jobs.scheduler.election import try_acquire_sweep_lock

        assert self._stop is not None
        scheduler = Scheduler(SchedulerConfig(), session_factory)
        conn = await leader_engine.connect()
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        is_leader = False
        try:
            while not self._draining:
                try:
                    if not is_leader:
                        is_leader = await try_acquire_sweep_lock(conn)
                    if is_leader:
                        await scheduler.sweep()
                except Exception:  # pragma: no cover - best-effort housekeeping loop
                    pass
                await self._sleep(scheduler.config.sweep_interval)
        finally:
            await conn.close()

    async def _sleep(self, interval: dt.timedelta) -> None:
        """Sleep, but wake immediately on drain so loops exit promptly."""
        assert self._stop is not None
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=interval.total_seconds())
        except TimeoutError:
            pass

    def request_drain(self) -> None:
        """SIGTERM handler (ADR-0016): stop claiming and wake every loop. Full
        graceful-drain semantics (requeue in-flight as `requeued`) land with a
        later slice."""
        self._draining = True
        if self._stop is not None:
            self._stop.set()
