from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerConfig:
    dsn: str

    max_in_flight: int = 10

    poll_interval: dt.timedelta = dt.timedelta(seconds=5)

    lease_ttl: dt.timedelta = dt.timedelta(seconds=45)
    heartbeat_interval: dt.timedelta = dt.timedelta(seconds=10)

    drain_grace: dt.timedelta = dt.timedelta(seconds=30)

    scheduler_only: bool = False


@dataclass(frozen=True)
class SchedulerConfig:
    horizon: dt.timedelta = dt.timedelta(hours=24)

    sweep_interval: dt.timedelta = dt.timedelta(seconds=30)

    miss_grace: dt.timedelta = dt.timedelta(seconds=60)

    stale_threshold: dt.timedelta = dt.timedelta(minutes=5)

    @property
    def effective_miss_grace(self) -> dt.timedelta:
        return max(self.miss_grace, self.sweep_interval)
