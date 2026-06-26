"""Transactional enqueue (ADR-0001/0006). Creates a Run *inside the caller's own
SQLAlchemy session*, so the Run commits atomically with the surrounding business
write — commit means it runs, rollback means it never existed.

Critical: this happens in the APP process at creation time. It does not execute
anything (ADR-0001 two-process design). The enqueued Run sits `pending` until a
Worker process — running `thrum worker` somewhere, pointed at the same Postgres
— claims it. Pass IDs, not live ORM objects (ADR-0006); the Task re-fetches.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from thrum.jobs.models import Run, RunStatus, Trigger
from thrum.jobs.registry import Task

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def enqueue(session: Session, task: Task | str, **inputs: Any) -> Run:
    """Insert a `pending` Run on the caller's session. Does not commit — the
    caller commits as part of their own transaction (that *is* the point)."""
    key = task.key if isinstance(task, Task) else task
    namespace, _, name = key.rpartition(".")
    run = Run(
        task_namespace=namespace,
        task_name=name,
        trigger=Trigger.enqueue,
        status=RunStatus.pending,
        inputs=inputs,
        # next_attempt_at left NULL == claimable immediately; backoff sets it later.
    )
    session.add(run)
    return run
