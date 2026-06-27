"""Operation, Task, and Registry (ADR-0023 / CONTEXT). A `Registry` declares a
`namespace` once; an `@registry.operation` inherits that namespace, and a bare
`@operation` falls into the process-shared `default` registry. Identity is
`namespace.name`, user-owned and stable across refactors — never derived from
import path. The process-global view fails fast on a `namespace.name` collision
at decoration/import time so the misconfiguration cannot reach the Worker.

The `Operation` is the *authoring surface* — what the developer writes and what
`.enqueue(...)` projects onto the queue. `Task` survives as an internal value
object (worker execution still talks Task-shape via duck-typed `.fn`/
`.retry_policy`); user code does not author Tasks directly.

SDK-core surface: stdlib + croniter only, no Worker/server imports (the
import-discipline law)."""

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
    """In-memory spec captured by the decorator — no I/O. Persisted to the
    `schedules` table by the startup reconcile (a later slice)."""

    cron: str
    timezone: str
    sla: dt.timedelta | None = None
    declared_duration: dt.timedelta | None = None
    start_grace: dt.timedelta | None = None


def _validate_cron(expr: str) -> None:
    if not croniter.is_valid(expr):
        raise ValueError(f"Invalid cron expression: {expr!r}")


def _validate_timezone(tz: str) -> None:
    if isinstance(tz, dt.timezone) or (isinstance(tz, str) and (tz.startswith("+") or tz.startswith("-"))):
        raise ValueError(
            f"Fixed-offset timezone {tz!r} is not allowed; use an IANA name "
            f"like 'America/Vancouver' (ADR-0015)"
        )
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, KeyError):
        raise ValueError(f"Unknown IANA timezone: {tz!r}") from None


@dataclass
class Task:
    """Internal value object carrying a callable's identity + execution config.
    No longer the authoring surface (that role moved to `Operation` — ADR-0023);
    survives as the value-object shape the Worker reads when it executes a Run.
    Worker tests that exercise the execute/record path directly still construct
    it."""

    fn: Callable[..., Any]
    namespace: str
    name: str
    max_attempts: int = 1
    timeout: float | None = None  # seconds; enforcement strength varies by context (ADR-0017)
    cpu_bound: bool = False        # opt into the process pool (ADR-0005)

    # Retry curve (ADR-0021). With max_attempts=1 these never fire, so the default
    # single-attempt behavior is unchanged; raising max_attempts opts into retries.
    retry_initial_delay: float = 1.0
    retry_max_delay: float = 300.0
    retry_backoff_factor: float = 2.0
    retry_jitter: bool = True

    declared_schedule: DeclaredSchedule | None = None

    @property
    def key(self) -> str:
        return f"{self.namespace}.{self.name}"

    @property
    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(
            max_attempts=self.max_attempts,
            initial_delay=self.retry_initial_delay,
            max_delay=self.retry_max_delay,
            backoff_factor=self.retry_backoff_factor,
            jitter=self.retry_jitter,
        )


@dataclass(frozen=True)
class Operation(Generic[P, R]):
    """The authored unit (ADR-0023). Holds resolved identity (`namespace.name`)
    and the captured signature; projects onto the durable queue via `.enqueue`.
    Still directly callable so unit-testing the function body does not require
    booting the queue. Carries the same execution config the Worker reads
    (`fn`, `retry_policy`) so a registered Operation can sit in
    `Registry._global` without a separate Task companion."""

    fn: Callable[P, R]
    namespace: str
    name: str
    signature: inspect.Signature = field(repr=False)
    signature_model: SignatureModel = field(repr=False)
    max_attempts: int = 1
    timeout: float | None = None
    cpu_bound: bool = False
    retry_initial_delay: float = 1.0
    retry_max_delay: float = 300.0
    retry_backoff_factor: float = 2.0
    retry_jitter: bool = True
    declared_schedule: DeclaredSchedule | None = None

    @property
    def key(self) -> str:
        return f"{self.namespace}.{self.name}"

    @property
    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(
            max_attempts=self.max_attempts,
            initial_delay=self.retry_initial_delay,
            max_delay=self.retry_max_delay,
            backoff_factor=self.retry_backoff_factor,
            jitter=self.retry_jitter,
        )

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R:
        return self.fn(*args, **kwargs)

    def enqueue(self, session: Session, **inputs: Any) -> Run:
        """Insert a `pending` Run on the caller's session. Validates `inputs`
        against the operation's **input schema** (the data-only signature with
        capability params stripped) so a missing or misspelled input — and a
        capability-named input — fails at the call, not later in the worker.
        Does not commit — the caller commits inside their own transaction
        (ADR-0001/0006)."""
        # Python's own argument-binding rules over the data-only signature
        # surface the right diagnostic (missing required, unexpected kwarg)
        # before any DB I/O.
        self.signature_model.input_schema.bind(**inputs)

        from thrum.jobs.enqueue import enqueue as _enqueue

        return _enqueue(session, self.key, **inputs)


class Registry:
    """A named grouping that declares a namespace once. Many Registries coexist
    (e.g. `billing`, `marketing`) so two functions named `send_receipts` don't
    collide — `billing.send_receipts` and `marketing.send_receipts` are
    distinct identities."""

    # Process-global view used to fail fast on namespace.name collisions. Shared
    # by named Registries AND the implicit `default` registry that bare
    # `@operation` resolves through, so the check is uniform.
    _global: dict[str, Operation] = {}
    _global_schedules: dict[str, DeclaredSchedule] = {}

    def __init__(self, namespace: str) -> None:
        self.namespace = namespace
        self.operations: dict[str, Operation] = {}

    def operation(
        self,
        fn: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        max_attempts: int = 1,
        timeout: float | None = None,
        cpu_bound: bool = False,
        retry_initial_delay: float = 1.0,
        retry_max_delay: float = 300.0,
        retry_backoff_factor: float = 2.0,
        retry_jitter: bool = True,
        schedule: str | None = None,
        timezone: str = "UTC",
        sla: dt.timedelta | None = None,
        declared_duration: dt.timedelta | None = None,
        start_grace: dt.timedelta | None = None,
    ) -> Any:
        """Register an Operation under this Registry's namespace. Identity is
        ``namespace.name``; pass ``name=`` to override the function name for
        the rare case where it isn't the identity you want. A duplicate
        ``namespace.name`` (against any other Registry in the process, or
        against the default registry) raises ``ValueError`` immediately.

        Pass ``schedule`` (a cron string) + ``timezone`` (an IANA name like
        ``"America/Vancouver"``) to declare a Schedule co-located with the
        Operation — reconciled into the ``schedules`` table on every Worker
        startup (ADR-0022). Cron / timezone validation fires here at import.

        Two deliberate v1 behaviors fall out of the reconcile design and are
        worth knowing up front:

        - **Old-schedule-wins for the already-materialized horizon.** Editing
          ``schedule`` or ``timezone`` in code updates the row at the next
          startup, but does **not** retract or regenerate Runs the sweep has
          already materialized over the ~24h horizon.
        - **Removing a declaration keeps history and revives.** Deleting the
          ``schedule`` arg (or the whole Operation) does not delete the
          `schedules` row; re-adding the same declaration revives the original
          row (reconcile keys on stable identity).
        """

        def register(func: Callable[..., Any]) -> Operation:
            declared = None
            if schedule is not None:
                _validate_cron(schedule)
                _validate_timezone(timezone)
                declared = DeclaredSchedule(
                    cron=schedule,
                    timezone=timezone,
                    sla=sla,
                    declared_duration=declared_duration,
                    start_grace=start_grace,
                )

            op_name = name or func.__name__
            key = f"{self.namespace}.{op_name}"
            if key in Registry._global:
                raise ValueError(f"Operation identity collision on {key!r}")

            sig = inspect.signature(func)
            # The capability-type registry is populated by execution-side
            # work (PRD-0002); until Compile lands, v1 classifies against
            # an empty set, so every keyword-only param falls through to
            # optional Data. The same code path tightens once Compile
            # hands the real registry in.
            sig_model = classify(func, capability_types=())
            op = Operation(
                fn=func,
                namespace=self.namespace,
                name=op_name,
                signature=sig,
                signature_model=sig_model,
                max_attempts=max_attempts,
                timeout=timeout,
                cpu_bound=cpu_bound,
                retry_initial_delay=retry_initial_delay,
                retry_max_delay=retry_max_delay,
                retry_backoff_factor=retry_backoff_factor,
                retry_jitter=retry_jitter,
                declared_schedule=declared,
            )

            if declared is not None:
                if key in Registry._global_schedules:
                    raise ValueError(
                        f"Duplicate schedule declaration for Operation {key!r}"
                    )
                Registry._global_schedules[key] = declared
            Registry._global[key] = op
            self.operations[op_name] = op
            return op

        return register(fn) if fn is not None else register


# The implicit Registry a bare `@operation` resolves through. Module-level so
# bare uses share collision state with named Registries (the fail-fast check is
# uniform across both paths).
_default_registry = Registry("default")


@overload
def operation(fn: Callable[P, R]) -> Operation[P, R]: ...


@overload
def operation(
    *,
    name: str | None = ...,
    max_attempts: int = ...,
    timeout: float | None = ...,
    cpu_bound: bool = ...,
    retry_initial_delay: float = ...,
    retry_max_delay: float = ...,
    retry_backoff_factor: float = ...,
    retry_jitter: bool = ...,
    schedule: str | None = ...,
    timezone: str = ...,
    sla: dt.timedelta | None = ...,
    declared_duration: dt.timedelta | None = ...,
    start_grace: dt.timedelta | None = ...,
) -> Callable[[Callable[P, R]], Operation[P, R]]: ...


def operation(
    fn: Callable[P, R] | None = None,
    **kwargs: Any,
) -> Operation[P, R] | Callable[[Callable[P, R]], Operation[P, R]]:
    """Mark a function as a Thrum Operation. A bare ``@operation`` falls into
    the ``default`` namespace via the process-shared default Registry —
    identity is ``namespace.name``, user-owned and stable across refactors.
    Pass ``name=`` to override the function name as the identity.

    Equivalent to ``@_default_registry.operation`` — the same fail-fast
    collision check applies."""
    return _default_registry.operation(fn, **kwargs)
