from __future__ import annotations

import datetime as dt
import random
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
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
    n = max(failure_index, 1)
    ceiling = min(policy.max_delay, policy.initial_delay * policy.backoff_factor ** (n - 1))
    seconds = ceiling * rng() if policy.jitter else ceiling
    return dt.timedelta(seconds=seconds)
