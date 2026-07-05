"""Worker-side orchestration of one Execution end to end.

Ties the Worker's claimed work to the framework's Execution Scope: thaw the
Caller, open the scope, record Effects, run the Operation, and write the result
and Effects into the one transaction the scope commits. A failure rolls that
transaction back and is recorded separately, so effects never outlive a failure.
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
    """Execute one claimed Run, recording its success or failure durably.

    Success writes the result and Effects into the scope's transaction and lets
    it commit. Any failure — unknown Operation, malformed Caller, or a raising
    body — is captured, the execution transaction rolled back, and the failure
    recorded in a fresh transaction so no partial effects survive.
    """
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
                await recorder.write(bound.session, claimed.attempt_id)
                await record_result(
                    bound.session,
                    claimed,
                    ExecutionResult(AttemptOutcome.succeeded, _as_run_output(output), None),
                    operation,
                )
                recorded_success = True
            except SerializationContractError as exc:
                failure = str(exc)
                raise
            except Exception:
                failure = traceback.format_exc()
                raise
    except Exception:
        # A raise after success means the commit itself failed; the result was
        # never durably recorded, so surface it rather than masking it as an
        # Operation failure.
        if recorded_success:
            raise
        if failure is None:
            failure = traceback.format_exc()

    # Recorded in a new transaction: the execution transaction has rolled back.
    if failure is not None:
        await _record_failure(session_factory, claimed, operation, failure)


def _as_run_output(output: Any) -> dict | None:
    """Keep a dict output for JSONB storage; discard any other shape."""
    return output if isinstance(output, dict) else None


async def _record_failure(
    session_factory: async_sessionmaker,
    claimed: ClaimedRun,
    operation: Operation | None,
    error: str,
) -> None:
    """Record a failed outcome for the Attempt in its own transaction."""
    async with session_factory() as session, session.begin():
        await record_result(
            session,
            claimed,
            ExecutionResult(AttemptOutcome.failed, None, error),
            operation,
        )
