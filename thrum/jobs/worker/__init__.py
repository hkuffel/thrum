"""The Worker process — claim due work, execute it, and coordinate roles.

A Worker runs three concurrent loops: the main claim-and-execute loop, a
heartbeat loop renewing its Leases, and a sweep loop that runs the Scheduler
role whenever it holds the election lock. Claiming and executing are split so a
crash between them leaves the Lease to expire and the Run reclaimable.
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
    """Claim a batch of due Runs and execute each in its own scope.

    Claiming commits in one short transaction; each Run then executes in a
    fresh transaction, so a slow execution never holds the claim's row locks.

    Args:
        limit: Maximum Runs to claim this pass.
        operations: Registry to resolve claimed work against; ignored when
            ``app`` is given.
        app: A compiled App supplying both operations and Providers.

    Returns:
        The number of Runs claimed and executed.
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
    """A long-running Worker process bound to one Postgres and operation set.

    Attributes:
        id: A process-unique identifier stamped onto claimed Attempts.
    """

    def __init__(self, config: WorkerConfig, app: App | None = None) -> None:
        self.config = config
        self.app = app if app is not None else App()
        self.id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._draining = False
        self._stop: asyncio.Event | None = None

    async def run(self) -> None:
        """Boot, then run the claim, heartbeat, and sweep loops until drained.

        The three loops get separate engines so a stalled execution can never
        starve heartbeat renewals or the sweep of their connections.
        """
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from thrum.jobs.db.engine import make_async_engine

        self._stop = asyncio.Event()
        if self._draining:
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
        """Compile the App and reconcile declared Schedules before serving work."""
        self.app.compile()
        await self._assert_schedules(session_factory)

    async def _assert_schedules(self, session_factory: async_sessionmaker) -> None:
        """Upsert this Worker's declared Schedules into the durable table."""
        from thrum.jobs.scheduler.reconcile import assert_declared_schedules

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
        """Renew this Worker's Leases on a cadence until drained.

        A failed renewal is swallowed: one missed beat must not kill the loop,
        and a sustained lapse is meant to let the Reaper reclaim the work.
        """
        assert self._stop is not None
        while not self._draining:
            try:
                async with session_factory() as session, session.begin():
                    await renew_leases(session, self.id, self.config.lease_ttl)
            except Exception:
                pass
            await self._sleep(self.config.heartbeat_interval)

    async def _sweep_loop(self, leader_engine, session_factory: async_sessionmaker) -> None:
        """Contend for the Scheduler lock and sweep while holding it.

        The lock connection runs in AUTOCOMMIT so the advisory lock is held on
        the session itself, not inside a transaction that would release it on
        commit. Once leader, the role is kept until the process exits.
        """
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
                except Exception:
                    pass
                await self._sleep(scheduler.config.sweep_interval)
        finally:
            await conn.close()

    async def _sleep(self, interval: dt.timedelta) -> None:
        """Sleep for ``interval``, waking early if a drain is requested."""
        assert self._stop is not None
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=interval.total_seconds())
        except TimeoutError:
            pass

    def request_drain(self) -> None:
        """Signal the Worker to stop claiming and wind down its loops."""
        self._draining = True
        if self._stop is not None:
            self._stop.set()
