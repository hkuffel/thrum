"""Tunable configuration for the Worker and Scheduler roles."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerConfig:
    """Settings for a Worker process.

    Attributes:
        dsn: The Postgres connection string.
        max_in_flight: Ceiling on concurrently executing Runs.
        poll_interval: Wait between Dispatch polls when no work is claimed.
        lease_ttl: Lease lifetime; the Reaper reclaims a Run whose Lease lapses.
        heartbeat_interval: Cadence for renewing a running Run's Lease. Must be
            comfortably shorter than ``lease_ttl``.
        drain_grace: How long a shutting-down Worker waits for in-flight Runs
            before releasing them.
        scheduler_only: Dedicate this Worker to the Scheduler role, claiming no
            execution work.
    """

    dsn: str

    max_in_flight: int = 10

    poll_interval: dt.timedelta = dt.timedelta(seconds=5)

    lease_ttl: dt.timedelta = dt.timedelta(seconds=45)
    heartbeat_interval: dt.timedelta = dt.timedelta(seconds=10)

    drain_grace: dt.timedelta = dt.timedelta(seconds=30)

    scheduler_only: bool = False


@dataclass(frozen=True)
class SchedulerConfig:
    """Settings for the Scheduler role's materialization and reconciliation.

    Attributes:
        horizon: How far ahead future Runs are pre-materialized — also the
            missed-detection durability window.
        sweep_interval: Cadence of the reconciliation sweep.
        miss_grace: Operational anti-jitter floor an unstarted Run must exceed
            past its Fire Time before it is marked Missed.
        stale_threshold: How long since a heartbeat before a Scheduler lock
            holder is treated as dead.
    """

    horizon: dt.timedelta = dt.timedelta(hours=24)

    sweep_interval: dt.timedelta = dt.timedelta(seconds=30)

    miss_grace: dt.timedelta = dt.timedelta(seconds=60)

    stale_threshold: dt.timedelta = dt.timedelta(minutes=5)

    @property
    def effective_miss_grace(self) -> dt.timedelta:
        """Miss grace floored at one sweep interval, so jitter can't manufacture a Missed."""
        return max(self.miss_grace, self.sweep_interval)
