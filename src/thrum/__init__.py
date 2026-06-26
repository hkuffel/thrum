"""thrum — authoring surface (ADR-0023 / PRD-0001).

A developer authors one thing — an `@operation` — and reaches the queue by
projecting it (`op.enqueue(session, **inputs)`). This module ships the keystone
slice of that contract: identity (`namespace.name`, user-owned and stable across
refactors), the captured signature, and the `enqueue` projection that writes a
`pending` Run on the caller's SQLAlchemy session without committing. Later
slices fill in the data/capability split, `@operation(...)` config, the
`op.schedule(...)` projection, and the Compile finalizer.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, ParamSpec, TypeVar, overload

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from thrum.jobs.models import Run

P = ParamSpec("P")
R = TypeVar("R")

__version__ = "0.0.2"


_DEFAULT_NAMESPACE = "default"


@dataclass(frozen=True)
class Operation(Generic[P, R]):
    """The authored unit (ADR-0023). Holds resolved identity (`namespace.name`)
    and the captured signature, and projects onto the durable queue via
    `.enqueue`. The Operation is still directly callable — the decorator must
    not break unit-testing the function body.

    This slice treats every parameter as required data; the data/capability
    classification arrives in a later slice (PRD-0001).
    """

    fn: Callable[P, R]
    namespace: str
    name: str
    signature: inspect.Signature = field(repr=False)

    @property
    def key(self) -> str:
        return f"{self.namespace}.{self.name}"

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        return self.fn(*args, **kwargs)

    def enqueue(self, session: Session, **inputs: Any) -> Run:
        """Insert a `pending` Run on the caller's session. Validates `inputs`
        against the operation's signature so a missing or misspelled input fails
        at the call, not later in the worker. Does not commit — the caller
        commits inside their own transaction (ADR-0001/0006)."""
        # Trigger Python's own argument-binding rules so missing/extra/duplicate
        # parameters surface as TypeError before any DB I/O.
        self.signature.bind(**inputs)

        from thrum.jobs.enqueue import enqueue as _enqueue

        return _enqueue(session, self.key, **inputs)


@overload
def operation(fn: Callable[P, R]) -> Operation[P, R]: ...


@overload
def operation() -> Callable[[Callable[P, R]], Operation[P, R]]: ...


def operation(
    fn: Callable[P, R] | None = None,
) -> Operation[P, R] | Callable[[Callable[P, R]], Operation[P, R]]:
    """Mark a function as a Thrum operation. A bare `@operation` falls into the
    `default` namespace; identity is `namespace.name` — user-owned, stable
    across refactors, never derived from the import path.

    Decoration captures the signature so `op.enqueue(...)` can validate inputs
    eagerly. The parameterized form (`@operation(timeout=, ...)`) is reserved
    for later slices; bare form is the keystone tracer bullet."""

    def wrap(func: Callable[P, R]) -> Operation[P, R]:
        return Operation(
            fn=func,
            namespace=_DEFAULT_NAMESPACE,
            name=func.__name__,
            signature=inspect.signature(func),
        )

    if fn is None:
        return wrap
    return wrap(fn)
