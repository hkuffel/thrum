"""Retry policy and backoff curve (ADR-0021): capped exponential with full jitter.

Full jitter (uniform in `[0, ceiling]`, per AWS "Exponential Backoff And Jitter")
exists to break synchronized retries: cron materializes occurrences in lockstep
(ADR-0003), so a batch failing against one flapping downstream would otherwise
retry in lockstep forever. The curve is pure with respect to the clock and DB —
`backoff_delay` returns a `timedelta` Record adds to Postgres `now()` — and its
random source is injectable so jitter is deterministic under test.

Stdlib only, no Worker or server imports (import-discipline law).
"""

from __future__ import annotations

import datetime as dt
import random
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    """An Operation's budget + backoff curve (ADR-0021). Defaults are the
    simple-correct beachhead: a 1→2→4→…→300s envelope with full jitter on.
    `max_attempts` is the Attempt budget of Operation-attributable tries
    (ADR-0020), not a backoff knob — with
    the default of 1 the curve never fires, so single-attempt behavior is
    unchanged."""

    max_attempts: int = 1
    initial_delay: float = 1.0  # seconds before the first retry
    max_delay: float = 300.0  # ceiling on the exponential envelope
    backoff_factor: float = 2.0  # exponential base
    jitter: bool = True  # full jitter: spread over [0, ceiling]


def backoff_delay(
    failure_index: int,
    policy: RetryPolicy,
    rng: Callable[[], float] = random.random,
) -> dt.timedelta:
    """Delay before the retry that follows the `failure_index`-th Operation failure
    (1-indexed: 1 is the first failure). The ceiling is
    `min(max_delay, initial_delay × factor^(failure_index − 1))`; with jitter on
    the actual delay is uniform in `[0, ceiling]` (`rng()` ∈ [0, 1)).

    `failure_index` is clamped at ≥ 1 so a nonsensical 0/negative still yields the
    first-step delay rather than a degenerate exponent."""
    n = max(failure_index, 1)
    ceiling = min(policy.max_delay, policy.initial_delay * policy.backoff_factor ** (n - 1))
    seconds = ceiling * rng() if policy.jitter else ceiling
    return dt.timedelta(seconds=seconds)
