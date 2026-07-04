"""Runtime configuration. Defaults are the simple-correct beachhead values."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerConfig:
    dsn: str

    # Cap on Runs claimed per poll. (True claim-to-capacity backpressure —
    # claiming only max_in_flight - in_flight — is not yet built; dispatch is
    # serial, so in-flight never exceeds 1.)
    max_in_flight: int = 10

    # Dispatch (ADR-0008): polling is the source of truth; interruptible sleep so
    # LISTEN/NOTIFY can short-circuit it later without redesign.
    poll_interval: dt.timedelta = dt.timedelta(seconds=5)

    # Lease / heartbeat (ADR-0013). Heartbeat frequently relative to the TTL so a
    # transient stall (GC pause, DB blip) does not trigger a false reap.
    lease_ttl: dt.timedelta = dt.timedelta(seconds=45)
    heartbeat_interval: dt.timedelta = dt.timedelta(seconds=10)

    # Graceful drain: align with the orchestrator's terminationGracePeriod.
    drain_grace: dt.timedelta = dt.timedelta(seconds=30)

    # Whether this Worker may contend for the scheduler advisory lock (ADR-0007).
    scheduler_only: bool = False


@dataclass(frozen=True)
class SchedulerConfig:
    # Eager materialization horizon (ADR-0003): the missed-detection durability
    # window AND the tzdata-safety window (ADR-0015).
    horizon: dt.timedelta = dt.timedelta(hours=24)

    # Cadence of the reconciliation sweep (materialize + mark missed + reap).
    sweep_interval: dt.timedelta = dt.timedelta(seconds=30)

    # Miss-detection Grace (CONTEXT.md): an internal operational floor a `scheduled`
    # Run must exceed past its fire_time before the sweep declares it `missed`, not
    # the per-Schedule Start Grace SLO knob. Floored at ≥ one sweep interval (see
    # `effective_miss_grace`) so scheduling jitter cannot manufacture a false
    # `missed` on a hard terminal transition.
    miss_grace: dt.timedelta = dt.timedelta(seconds=60)

    # Stale-declaration threshold (ADR-0022): how long after last_declared_at
    # before the leader sweep clears a Schedule's declaration gate. Must exceed
    # a full deploy+drain cycle so rolling deploys don't thrash gates.
    stale_threshold: dt.timedelta = dt.timedelta(minutes=5)

    @property
    def effective_miss_grace(self) -> dt.timedelta:
        """The applied Miss-detection Grace, floored at one sweep interval. A
        Run claimable-but-not-yet-claimed when a sweep fires must never look
        `missed`, so the grace can never drop below the sweep cadence."""
        return max(self.miss_grace, self.sweep_interval)
