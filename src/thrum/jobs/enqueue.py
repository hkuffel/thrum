"""Transactional enqueue. Creates a Run inside the caller's own SQLAlchemy
session, so the Run commits atomically with the surrounding business write —
commit means it runs, rollback means it never existed.

Critical: this happens in the APP process at creation time and executes nothing;
execution is a separate two-process concern. The enqueued Run sits `pending`
until a Worker process — running `thrum worker` somewhere, pointed at the same
Postgres — claims it. Pass IDs, not live ORM objects; the Task re-fetches.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from thrum.jobs.models import Run, RunStatus, Trigger
from thrum.jobs.registry import Task
from thrum.jobs.serialization import ensure_serializable

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def enqueue(
    session: Session,
    task: Task | str,
    *,
    max_attempts: int | None = None,
    **inputs: Any,
) -> Run:
    """Insert a `pending` Run on the caller's session. Does not commit — the
    caller commits as part of their own transaction (that is the point).

    `max_attempts` carries a per-call durability override set by
    `op.enqueue(..., retries=N)` as N+1; NULL means inherit the Operation's
    default at execution time. Only the queue projection (enqueue) sets this;
    non-durable projections (future HTTP) must leave it NULL."""
    key = task.key if isinstance(task, Task) else task

    # The queue's serialization boundary: inputs land in the `inputs` JSONB
    # column, so a non-serializable value (a live ORM object instead of an ID)
    # must fail here with the contract error, not later as a cryptic encoder
    # failure when the Run is written.
    for input_name, value in inputs.items():
        ensure_serializable(value, owner=key, role=f"input {input_name!r}")

    namespace, _, name = key.rpartition(".")
    run = Run(
        operation_namespace=namespace,
        operation_name=name,
        trigger=Trigger.enqueue,
        status=RunStatus.pending,
        inputs=inputs,
        max_attempts=max_attempts,
        # next_attempt_at left NULL == claimable immediately; backoff sets it later.
    )
    session.add(run)
    return run
