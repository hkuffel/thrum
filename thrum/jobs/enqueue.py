"""Enqueue — creating a queued Run from inside the caller's app process."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from thrum.jobs.models import Run, RunStatus, Trigger
from thrum.jobs.providers import Caller
from thrum.jobs.registry import Operation
from thrum.jobs.serialization import ensure_serializable

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def enqueue(
    session: Session,
    operation: Operation | str,
    *,
    max_attempts: int | None = None,
    **inputs: Any,
) -> Run:
    """Insert a pending Run on the caller's transaction, uncommitted.

    The Run is added to ``session`` but not committed, so it lands atomically
    with the caller's own writes. The Caller is frozen onto the Run now and
    thawed at execution, since the queue separates the two in time and process.

    Args:
        operation: The Operation or its ``namespace.name`` key.
        max_attempts: The retry budget to stamp, or ``None`` for the default.
        **inputs: The Operation's Data parameters.

    Returns:
        The added-but-uncommitted Run.

    Raises:
        SerializationContractError: If any input is not JSON-serializable.
    """
    key = operation.key if isinstance(operation, Operation) else operation

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
        caller=Caller.system().freeze(),
    )
    session.add(run)
    return run
