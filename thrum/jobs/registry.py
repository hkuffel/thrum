"""The Operation primitive and the Registry that owns its identity.

The ``@operation`` decorator captures a function's signature and registers its
``namespace.name`` identity at import time — phase one of Compile's two-phase
validation, which fails fast on a collision or a malformed Schedule. A Registry
declares a namespace once; Operations attach to it and inherit it.
"""

from __future__ import annotations

import datetime as dt
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, ParamSpec, TypeVar, overload
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from thrum.jobs.retry import RetryPolicy
from thrum.jobs.signature import SignatureModel, classify

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from thrum.jobs.models import Run

P = ParamSpec("P")
R = TypeVar("R")


@dataclass(frozen=True)
class DeclaredSchedule:
    """A Schedule as declared in code, before it is materialized into Runs.

    Attributes:
        timezone: An IANA name (``America/Denver``), never a fixed offset — the
            cron + tz pair is the durable recurrence intent.
        sla: The deadline facet of the Expectation, if declared.
        declared_duration: How long a Run is expected to take.
        start_grace: How tardy a start may be before the Run is flagged Late.
    """

    cron: str
    timezone: str
    sla: dt.timedelta | None = None
    declared_duration: dt.timedelta | None = None
    start_grace: dt.timedelta | None = None


def _validate_cron(expr: str) -> None:
    """Reject a malformed cron expression at declaration time."""
    if not croniter.is_valid(expr):
        raise ValueError(f"Invalid cron expression: {expr!r}")


def _validate_timezone(tz: str) -> None:
    """Reject a fixed-offset or unknown timezone, requiring an IANA name.

    A fixed offset can't express DST, so a Schedule must name a zone whose
    offset varies with the calendar.
    """
    is_fixed_offset = isinstance(tz, dt.timezone) or (
        isinstance(tz, str) and (tz.startswith("+") or tz.startswith("-"))
    )
    if is_fixed_offset:
        raise ValueError(
            f"Fixed-offset timezone {tz!r} is not allowed; use an IANA name like 'America/Denver'"
        )
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, KeyError):
        raise ValueError(f"Unknown IANA timezone: {tz!r}") from None


@dataclass(frozen=True)
class Operation(Generic[P, R]):
    """The authored unit — a function projected onto transports.

    An Operation is the sole execution-config surface the Worker reads:
    execution nature (``timeout``, ``cpu_bound``) is intrinsic; the retry knobs
    are a durability default that durable projections inherit. Triggers are
    separate — an Operation declares 0..N Schedules via ``schedule``.

    Attributes:
        namespace: Inherited from the owning Registry; half of the identity.
        signature_model: The Data/Capability classification, kept for Compile
            and Enqueue validation.
        cpu_bound: Marks an Operation that must run in a process pool rather than
            the async or thread pool.
        declared_schedules: Excluded from equality — two Operations are the same
            regardless of the triggers hung off them.
    """

    fn: Callable[P, R]
    namespace: str
    name: str
    signature: inspect.Signature = field(repr=False)
    signature_model: SignatureModel = field(repr=False)
    retries: int = 0
    timeout: float | None = None
    cpu_bound: bool = False
    retry_initial_delay: float = 1.0
    retry_max_delay: float = 300.0
    retry_backoff_factor: float = 2.0
    retry_jitter: bool = True
    declared_schedules: list[DeclaredSchedule] = field(
        default_factory=list,
        repr=False,
        compare=False,
    )

    @property
    def key(self) -> str:
        """The ``namespace.name`` identity."""
        return f"{self.namespace}.{self.name}"

    @property
    def retry_policy(self) -> RetryPolicy:
        """The durability policy assembled from the retry knobs."""
        return RetryPolicy(
            max_attempts=self.retries + 1,
            initial_delay=self.retry_initial_delay,
            max_delay=self.retry_max_delay,
            backoff_factor=self.retry_backoff_factor,
            jitter=self.retry_jitter,
        )

    def schedule(
        self,
        cron: str,
        tz: str = "UTC",
        *,
        sla: dt.timedelta | None = None,
        declared_duration: dt.timedelta | None = None,
        start_grace: dt.timedelta | None = None,
    ) -> DeclaredSchedule:
        """Declare a recurring trigger co-located under the Operation.

        Validates the cron and timezone eagerly so a typo fails at import, and
        registers the declaration both on this Operation and in the global
        schedule table the scheduler reads.

        Raises:
            ValueError: If the cron or timezone is invalid, or the Operation
                already declares this cron.
        """
        _validate_cron(cron)
        _validate_timezone(tz)
        if any(existing.cron == cron for existing in self.declared_schedules):
            raise ValueError(f"Duplicate schedule cron {cron!r} on operation {self.key!r}")
        declared = DeclaredSchedule(
            cron=cron,
            timezone=tz,
            sla=sla,
            declared_duration=declared_duration,
            start_grace=start_grace,
        )
        self.declared_schedules.append(declared)
        Registry._global_schedules.setdefault(self.key, []).append(declared)
        return declared

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        """Call the underlying function directly, bypassing any projection."""
        return self.fn(*args, **kwargs)

    def enqueue(self, session: Session, *, retries: int | None = None, **inputs: Any) -> Run:
        """Create a queued Run of this Operation on the caller's transaction.

        The queue projection: inputs are validated against the input schema
        before a Run is inserted on the caller's own ``session``, so the Run
        commits atomically with the surrounding business write.

        Args:
            session: The caller's transactional handle, used to insert the Run.
            retries: Override the Operation's retry budget for this Run only.
            **inputs: The Operation's Data parameters.

        Raises:
            TypeError: If ``inputs`` do not satisfy the input schema.
        """
        self.signature_model.input_schema.bind(**inputs)

        # Imported here to break the registry <-> enqueue import cycle.
        from thrum.jobs.enqueue import enqueue as _enqueue

        max_attempts = retries + 1 if retries is not None else None
        return _enqueue(session, self.key, max_attempts=max_attempts, **inputs)


class Registry:
    """A named grouping that declares a namespace once for its Operations.

    Identity is tracked in two places: per-Registry (``operations``) and in a
    process-global table (``_global``) that backs fail-fast collision detection
    across every Registry in the program.
    """

    _global: dict[str, Operation] = {}
    _global_schedules: dict[str, list[DeclaredSchedule]] = {}

    def __init__(self, namespace: str) -> None:
        self.namespace = namespace
        self.operations: dict[str, Operation] = {}

    def operation(
        self,
        fn: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        retries: int = 0,
        timeout: float | None = None,
        cpu_bound: bool = False,
        retry_initial_delay: float = 1.0,
        retry_max_delay: float = 300.0,
        retry_backoff_factor: float = 2.0,
        retry_jitter: bool = True,
    ) -> Any:
        """Decorator that registers a function as an Operation on this namespace.

        Usable bare (``@registry.operation``) or called
        (``@registry.operation(retries=3)``). ``name`` defaults to the function
        name.

        Raises:
            ValueError: If the resulting ``namespace.name`` is already taken.
        """

        def register(func: Callable[..., Any]) -> Operation:
            op_name = name or func.__name__
            key = f"{self.namespace}.{op_name}"
            if key in Registry._global:
                raise ValueError(f"Operation identity collision on {key!r}")

            sig = inspect.signature(func)
            sig_model = classify(func, capability_types=())
            op = Operation(
                fn=func,
                namespace=self.namespace,
                name=op_name,
                signature=sig,
                signature_model=sig_model,
                retries=retries,
                timeout=timeout,
                cpu_bound=cpu_bound,
                retry_initial_delay=retry_initial_delay,
                retry_max_delay=retry_max_delay,
                retry_backoff_factor=retry_backoff_factor,
                retry_jitter=retry_jitter,
            )

            Registry._global[key] = op
            self.operations[op_name] = op
            return op

        return register(fn) if fn is not None else register


_default_registry = Registry("default")


@overload
def operation(fn: Callable[P, R]) -> Operation[P, R]: ...


@overload
def operation(
    *,
    name: str | None = ...,
    retries: int = ...,
    timeout: float | None = ...,
    cpu_bound: bool = ...,
    retry_initial_delay: float = ...,
    retry_max_delay: float = ...,
    retry_backoff_factor: float = ...,
    retry_jitter: bool = ...,
) -> Callable[[Callable[P, R]], Operation[P, R]]: ...


def operation(
    fn: Callable[P, R] | None = None,
    **kwargs: Any,
) -> Operation[P, R] | Callable[[Callable[P, R]], Operation[P, R]]:
    """Register an Operation in the ``default`` namespace — the one-file start."""
    return _default_registry.operation(fn, **kwargs)
