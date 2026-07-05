"""Retry budget and backoff schedule for a Run's Attempts."""

from __future__ import annotations

import datetime as dt
import random
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    """The durability policy an Operation contributes to its durable projections.

    Attributes:
        max_attempts: Total Attempts a Run may make before it fails for good.
            The default of 1 means no retry.
        initial_delay: Backoff ceiling in seconds after the first failure.
        max_delay: Cap the exponential growth never exceeds, in seconds.
        backoff_factor: The base the delay is raised by on each failure.
        jitter: Whether to spread retries randomly under the ceiling to avoid a
            thundering herd of Runs that all failed at once.
    """

    max_attempts: int = 1
    initial_delay: float = 1.0
    max_delay: float = 300.0
    backoff_factor: float = 2.0
    jitter: bool = True


def backoff_delay(
    failure_index: int,
    policy: RetryPolicy,
    rng: Callable[[], float] = random.random,
) -> dt.timedelta:
    """Compute how long to wait before the next Attempt.

    Args:
        failure_index: The number of failures so far (1 after the first).
        policy: The Operation's retry policy.
        rng: Source of the jitter fraction, injectable for deterministic tests.

    Returns:
        The delay to wait, capped at ``policy.max_delay`` and, when jitter is
        on, drawn uniformly between zero and that exponential ceiling.
    """
    n = max(failure_index, 1)
    ceiling = min(policy.max_delay, policy.initial_delay * policy.backoff_factor ** (n - 1))
    seconds = ceiling * rng() if policy.jitter else ceiling
    return dt.timedelta(seconds=seconds)
