"""The Worker: a standalone process that loads the user's Operation
code, claims due Runs from Postgres, and executes them out-of-process on one
asyncio loop.

Around the claim → execute → record path it runs the lease-recovery substrate: a
per-Worker heartbeat renewing open leases (ADR-0013), leader election for the
scheduler role (ADR-0007), and the leader-gated sweep that materializes, marks
missed, and reaps (ADR-0013/0020). Claim serves both the `pending` and `scheduled`
arms, so cron work flows end to end, and an Operation-attributable failure retries
with backoff until its Attempt budget is spent rather than failing on first raise
(ADR-0020/0021).
"""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from typing import TYPE_CHECKING

from thrum.jobs.app import App
from thrum.jobs.config import SchedulerConfig, WorkerConfig
from thrum.jobs.worker.claim import claim_runs
from thrum.jobs.worker.heartbeat import renew_leases
from thrum.jobs.worker.scope import run_scoped

if TYPE_CHECKING:
    import datetime as dt

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from thrum.jobs.providers import Provider
    from thrum.jobs.registry import Operation


async def run_once(
    session_factory: async_sessionmaker,
    worker_id: str,
    limit: int,
    lease_ttl: dt.timedelta,
    operations: dict[str, Operation] | None = None,
    *,
    app: App | None = None,
) -> int:
    """One claim → scope.run pass. Returns the number of Runs processed.

    The Claim transaction commits (releasing row locks) before any Operation
    executes; the Execution Scope then runs each Run in its own transaction with injected
    Capabilities and records its outcome (ADR-0024). The testable seam beneath `Worker.run`.

    `app` supplies both the operation and Provider registries. The bare
    `operations` mapping is the no-capability path the lower-level tracer tests
    drive directly; without an App there are no Providers to inject.
    """
    if app is not None:
        operations = app.operations
        providers: dict[type, Provider] = app.providers
    else:
        operations = operations or {}
        providers = {}

    async with session_factory() as session, session.begin():
        claimed = await claim_runs(session, worker_id, limit, lease_ttl)

    for item in claimed:
        await run_scoped(session_factory, item, operations, providers)

    return len(claimed)


class Worker:
    def __init__(self, config: WorkerConfig, app: App | None = None) -> None:
        self.config = config
        # The Worker is handed the App that owns the operation + Provider
        # registries; an unscoped default discovers the process-global registry
        # (ADR-0024). compile() at boot resolves Capabilities against it.
        self.app = app if app is not None else App()
        self.id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._draining = False
        self._stop: asyncio.Event | None = None

    async def run(self) -> None:
        """The Worker loop: claim → execute → record → interruptible sleep
        (polling is the source of truth, ADR-0008). Alongside it run two
        background loops for this Worker's whole life — the Heartbeat (every
        Worker) and the leader-gated sweep (whichever Worker holds the lock).

        Each loop gets its own engine so the three concerns never share a pooled
        connection (ADR-0013): an Operation holding an execution session open must not
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

        await self._boot(exec_factory)

        heartbeat = asyncio.create_task(self._heartbeat_loop(hb_factory))
        sweeper = asyncio.create_task(self._sweep_loop(leader_engine, sweep_factory))
        try:
            while not self._draining:
                processed = await run_once(
                    exec_factory,
                    self.id,
                    self.config.max_in_flight,
                    self.config.lease_ttl,
                    app=self.app,
                )
                if processed == 0:
                    await self._sleep(self.config.poll_interval)
        finally:
            self._stop.set()
            await asyncio.gather(heartbeat, sweeper, return_exceptions=True)
            await exec_engine.dispose()
            await hb_engine.dispose()
            await leader_engine.dispose()

    async def _boot(self, session_factory: async_sessionmaker) -> None:
        """`app.compile()` resolves Capabilities and fails fast on an unresolvable
        one; the claim loop must not start until it succeeds.
        The schedule reconcile then writes declared schedules."""
        self.app.compile()
        await self._assert_schedules(session_factory)

    async def _assert_schedules(self, session_factory: async_sessionmaker) -> None:
        from thrum.jobs.scheduler.reconcile import assert_declared_schedules

        # Reconcile only this App's own schedules, not the process-global
        # accumulator: a scoped App(registry=...) must not reconcile (and then
        # claim, and fail) another registry's schedules in the same process.
        declared = {
            op.key: op.declared_schedules
            for op in self.app.operations.values()
            if op.declared_schedules
        }
        if not declared:
            return
        async with session_factory() as session, session.begin():
            await assert_declared_schedules(session, declared)

    async def _heartbeat_loop(self, session_factory: async_sessionmaker) -> None:
        """Renew this Worker's open-Attempt leases every `heartbeat_interval`
        until drain. lease_ttl/3 so two missed ticks are needed to look orphaned."""
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
        sweep (materialize + mark missed + reap). The advisory-lock connection is
        held open for the whole loop, so Postgres releases the lock
        on this Worker's death and the next Worker wins (ADR-0007). AUTOCOMMIT
        keeps that connection out of an idle-in-transaction state between `try`s."""
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
        """SIGTERM handler: stop claiming and wake every loop. Full graceful-drain
        semantics (requeue in-flight as `requeued`) are not yet implemented."""
        self._draining = True
        if self._stop is not None:
            self._stop.set()
