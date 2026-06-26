"""Task and Registry (CONTEXT). A Registry declares a `namespace` once; Tasks
attach to it and inherit that namespace. Identity is `namespace.name`,
user-owned and stable across refactors — never derived from import path.
Uniqueness on the full pair is enforced at Worker/registry startup (fail fast).

SDK-core surface: stdlib + croniter only, no Worker/server imports (import-discipline law)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

from thrum.jobs.retry import RetryPolicy


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
    """A registered Python callable plus its config. The *code*. Does not execute
    on its own — something must create a Run of it (cron / enqueue / workflow)."""

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
        """The Task's budget + backoff curve as the value object Record uses to
        decide retry-vs-terminal on a Task failure (ADR-0021)."""
        return RetryPolicy(
            max_attempts=self.max_attempts,
            initial_delay=self.retry_initial_delay,
            max_delay=self.retry_max_delay,
            backoff_factor=self.retry_backoff_factor,
            jitter=self.retry_jitter,
        )


class Registry:
    """A named grouping that declares a namespace once. Many Registries coexist
    (e.g. `billing`, `marketing`) so two functions named `send_receipts` don't
    collide."""

    # Process-global view used to fail fast on namespace.name collisions.
    _global: dict[str, Task] = {}
    _global_schedules: dict[str, DeclaredSchedule] = {}

    def __init__(self, namespace: str) -> None:
        self.namespace = namespace
        self.tasks: dict[str, Task] = {}

    def task(
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
        """Register a Task. Pass `schedule` (a cron string) + `timezone` (an IANA
        name like ``"America/Vancouver"``) to declare a Schedule co-located with
        the Task — reconciled into the `schedules` table on every Worker startup
        (ADR-0022). Validation of the cron expression and timezone fires
        immediately at import; a typo or fixed-offset tz raises here, not silently
        at runtime.

        Two deliberate v1 behaviors fall out of the reconcile design and are
        worth knowing up front:

        - **Old-schedule-wins for the already-materialized horizon.** Editing
          ``schedule`` or ``timezone`` in code updates the row at the next
          startup, but does **not** retract or regenerate Runs the sweep has
          already materialized over the ~24h horizon. Your edit takes effect
          for occurrences materialized *after* it lands; the ≤horizon of
          already-scheduled Runs fire on the old rule. Pausing (removing the
          declaration) *is* honored at the next sweep — only retroactive
          retraction of already-created rows is deferred.
        - **Removing a declaration keeps history and revives.** Deleting the
          ``schedule`` arg (or the whole Task) does not delete the `schedules`
          row — the leader sweep clears its declaration gate once
          ``last_declared_at`` ages past ``stale_threshold``, but the row and
          its past Runs/missed occurrences survive so you can still answer
          "did this job run last week?" Re-adding the same declaration revives
          the original row (reconcile keys on stable Task identity).
        """

        def register(func: Callable[..., Any]) -> Task:
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

            t = Task(
                fn=func,
                namespace=self.namespace,
                name=name or func.__name__,
                max_attempts=max_attempts,
                timeout=timeout,
                cpu_bound=cpu_bound,
                retry_initial_delay=retry_initial_delay,
                retry_max_delay=retry_max_delay,
                retry_backoff_factor=retry_backoff_factor,
                retry_jitter=retry_jitter,
                declared_schedule=declared,
            )
            if t.key in Registry._global:
                raise ValueError(f"Task identity collision on {t.key!r}")
            if declared is not None:
                existing = Registry._global_schedules.get(t.key)
                if existing is not None:
                    raise ValueError(
                        f"Duplicate schedule declaration for Task {t.key!r}"
                    )
                Registry._global_schedules[t.key] = declared
            Registry._global[t.key] = t
            self.tasks[t.name] = t
            return t

        return register(fn) if fn is not None else register


# Module-level convenience registry under the default namespace.
_default = Registry("default")
task = _default.task
