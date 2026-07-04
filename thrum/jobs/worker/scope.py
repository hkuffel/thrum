"""The durable rim: run one claimed Run through the shared Execution Scope,
observing Effects and recording the Attempt outcome (ADR-0024).

The transport-agnostic core (`thrum.jobs.scope.ExecutionScope`) owns Capability
assembly, the execution transaction, and output validation. This rim adds what is
durable and the core knows nothing of:

    success -> Effects and the `succeeded` Attempt (with output) commit atomically
               with the body's own writes, inside the scope's transaction.
    failure -> the transaction rolls back, then the `failed` Attempt + retry state
               is recorded in a separate transaction, so the Run never hangs
               `running` (ADR-0024).

Worker death mid-execution raises `BaseException` (cancellation), not an Operation
failure: it propagates uncaught, nothing commits, and the Run stays `running` with
an open Attempt for the Reaper (ADR-0013).
"""

from __future__ import annotations

import traceback
from typing import TYPE_CHECKING, Any

from thrum.jobs.models import AttemptOutcome
from thrum.jobs.providers import Caller
from thrum.jobs.scope import ExecutionScope
from thrum.jobs.serialization import SerializationContractError
from thrum.jobs.worker.effects import EffectRecorder
from thrum.jobs.worker.execute import ExecutionResult
from thrum.jobs.worker.record import record_result

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from thrum.jobs.providers import Provider
    from thrum.jobs.registry import Operation
    from thrum.jobs.worker.claim import ClaimedRun


async def run_scoped(
    session_factory: async_sessionmaker,
    claimed: ClaimedRun,
    operations: dict[str, Operation],
    providers: dict[type, Provider],
) -> None:
    """Execute one claimed Run through the Execution Scope. A missing Operation, a
    malformed Caller, a raised body, or a non-serializable output becomes a `failed`
    Attempt recorded in a separate transaction — never propagated, so the Worker
    loop keeps turning."""
    operation = operations.get(claimed.operation_key)
    if operation is None:
        await _record_failure(
            session_factory,
            claimed,
            None,
            f"Operation {claimed.operation_key!r} is not registered in this Worker",
        )
        return

    try:
        caller = Caller.thaw(claimed.caller)
    except Exception:
        # A malformed frozen Caller is an unfillable scope, not a worker death:
        # record it `failed` so the Reaper never re-claims the bad row forever.
        await _record_failure(
            session_factory, claimed, operation, "Malformed frozen Caller on the Run"
        )
        return

    scope = ExecutionScope(session_factory, providers)
    failure: str | None = None
    recorded_success = False
    try:
        async with scope.open(operation, caller) as bound:
            try:
                async with EffectRecorder.observing(bound.session) as recorder:
                    output = await bound.run(claimed.inputs)
                # Only write after observing detaches, so the framework's writes are
                # never counted as the Operation's Effects.
                await recorder.write(bound.session, claimed.attempt_id)
                await record_result(
                    bound.session,
                    claimed,
                    ExecutionResult(AttemptOutcome.succeeded, _as_run_output(output), None),
                    operation,
                )
                recorded_success = True
            except SerializationContractError as exc:
                # Record its message, not a traceback; re-raise to roll back.
                failure = str(exc)
                raise
            except Exception:
                failure = traceback.format_exc()
                raise
    except Exception:
        # A commit already recorded `succeeded`; a Provider teardown raising during
        # unwind must propagate rather than overwrite it. Otherwise the raise is
        # either the body failure captured above, or a Provider setup failure to
        # record now — never a worker death, which is a `BaseException` left uncaught.
        if recorded_success:
            raise
        if failure is None:
            failure = traceback.format_exc()

    if failure is not None:
        await _record_failure(session_factory, claimed, operation, failure)


def _as_run_output(output: Any) -> dict | None:
    """`Run.output` is a JSONB object, so a non-dict body return is dropped on the
    durable path (a synchronous projection keeps the full return value)."""
    return output if isinstance(output, dict) else None


async def _record_failure(
    session_factory: async_sessionmaker,
    claimed: ClaimedRun,
    operation: Operation | None,
    error: str,
) -> None:
    """Record the `failed` Attempt and its retry-or-terminal decision in its own
    transaction, isolated from the rolled-back execution transaction (ADR-0024)."""
    async with session_factory() as session, session.begin():
        await record_result(
            session,
            claimed,
            ExecutionResult(AttemptOutcome.failed, None, error),
            operation,
        )
