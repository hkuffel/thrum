"""The Scheduler role and its single reconciliation sweep.

The Worker that wins the advisory lock runs this sweep on a cadence:
materialize future Runs, mark missed occurrences, reap orphaned Attempts, and
pause schedules no longer declared. One sweep, one transaction — orphan and
missed detection are one query family.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from thrum.jobs.config import SchedulerConfig
from thrum.jobs.scheduler.materialize import materialize_schedules
from thrum.jobs.scheduler.missed import mark_missed
from thrum.jobs.scheduler.reaper import reap_orphans
from thrum.jobs.scheduler.reconcile import pause_stale_schedules

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker


@dataclass(frozen=True)
class SweepResult:
    """Counts of what one sweep changed, for logging and tests."""

    materialized: int = 0
    missed: int = 0
    reaped: int = 0
    stale_paused: int = 0


class Scheduler:
    """Runs the reconciliation sweep for the elected Scheduler Worker."""

    def __init__(
        self,
        config: SchedulerConfig,
        session_factory: async_sessionmaker,
    ) -> None:
        self.config = config
        self._session_factory = session_factory

    async def sweep(self) -> SweepResult:
        """Run one reconciliation pass, all four duties in a single transaction."""
        async with self._session_factory() as session, session.begin():
            materialized = await materialize_schedules(session, self.config.horizon)
            missed = await mark_missed(session, self.config.effective_miss_grace)
            reaped = await reap_orphans(session)
            stale_paused = await pause_stale_schedules(session, self.config.stale_threshold)
        return SweepResult(
            materialized=materialized,
            missed=missed,
            reaped=reaped,
            stale_paused=stale_paused,
        )
