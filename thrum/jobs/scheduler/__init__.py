"""The scheduler is a leader-elected role. The Worker that wins a Postgres advisory lock
additionally runs this loop; the rest are warm
failover. No separate deployable process.

three duties:
materialize the horizon, mark missed occurrences, reap orphaned Attempts.
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
    """What one reconciliation tick did. A small record so the sweep can report
    each duty's effect (e.g. to a future Control Plane) without the caller poking
    at internals."""

    materialized: int = 0
    missed: int = 0
    reaped: int = 0
    stale_paused: int = 0


class Scheduler:
    def __init__(
        self,
        config: SchedulerConfig,
        session_factory: async_sessionmaker,
    ) -> None:
        self.config = config
        self._session_factory = session_factory

    async def sweep(self) -> SweepResult:
        """One reconciliation sweep. All passes are the same query family — "rows
        in the wrong state for the current clock" — and run in one transaction:

        1. materialize: expand each non-paused Schedule's cron over the horizon and
           INSERT ... ON CONFLICT DO NOTHING on (schedule_id, fire_time) (ADR-0003).
        2. mark missed: `scheduled` Runs past fire_time + Miss-detection-Grace that
           never went `running` -> `missed`, a first-class terminal (ADR-0003).
        3. reap: open Attempts (ended_at IS NULL) past their lease are orphans of a
           dead Worker — close `abandoned`, requeue the Run to `pending`/now()
           unconditionally (ADR-0013 / ADR-0020).

        Ordered materialize → missed → reap so a just-materialized occurrence is
        never spuriously swept in the same tick (its fire_time is in the future).
        Returns a `SweepResult` recording each duty's effect.
        """
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
