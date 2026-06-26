from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

__version__ = "0.0.1"


@dataclass(frozen=True)
class Operation(Generic[P, R]):
    fn: Callable[P, R]
    name: str

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        return self.fn(*args, **kwargs)


def operation(fn: Callable[P, R]) -> Operation[P, R]:
    """Mark a function as a Thrum operation.

    NOTE: this is the 23-line stub. The authoring contract it must grow into is
    fixed in docs/adr/0023-operation-task-convergence.md + docs/CONTEXT.md: the
    Operation is the sole authored unit (it absorbs the ported `Task`), with
    namespaced identity via a Registry, a keyword-only-AND-registered-type
    capability split, `@operation(timeout=, cpu_bound=, retries=)` config plus a
    co-located `op.schedule(...)`, an `op.enqueue(session, **inputs)` queue
    projection, and two-phase validation finalized at Compile (`app.compile()`).
    Not yet implemented — ADR-0023 is the spec.
    """
    return Operation(fn=fn, name=fn.__name__)
