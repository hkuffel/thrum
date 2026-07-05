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
    """Build an Operation directly, bypassing the decorator and its registration.

    Lets a test construct an Operation with an explicit identity without touching
    the global registry. ``max_attempts`` is expressed as a total and mapped to
    the Operation's ``retries`` (attempts minus one).
    """
    return Operation(
        fn=fn,
        namespace=namespace,
        name=name,
        signature=inspect.signature(fn),
        signature_model=classify(fn, capability_types=()),
        retries=max_attempts - 1,
        **config,
    )
