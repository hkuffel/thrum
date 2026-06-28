"""Test factories for the framework's value objects. The Worker/execution tests
drive the execute/record path directly, so they build an Operation without
booting the full registry."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from thrum.jobs.registry import Operation
from thrum.jobs.signature import classify


def make_operation(
    fn: Callable[..., Any],
    namespace: str,
    name: str,
    *,
    max_attempts: int = 1,
    **config: Any,
) -> Operation:
    """Build an Operation around `fn`, mapping the Attempt-budget term
    `max_attempts` onto the Operation's `retries` (= max_attempts - 1).
    Remaining execution config (`timeout`, `retry_initial_delay`, …) passes
    through. v1 callers register no capabilities, so capability params classify
    as data here just as they do in the real registry."""
    return Operation(
        fn=fn,
        namespace=namespace,
        name=name,
        signature=inspect.signature(fn),
        signature_model=classify(fn, capability_types=()),
        retries=max_attempts - 1,
        **config,
    )
