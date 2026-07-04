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
    return Operation(
        fn=fn,
        namespace=namespace,
        name=name,
        signature=inspect.signature(fn),
        signature_model=classify(fn, capability_types=()),
        retries=max_attempts - 1,
        **config,
    )
