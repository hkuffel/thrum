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
    """Mark a function as a Thrum operation."""
    return Operation(fn=fn, name=fn.__name__)
