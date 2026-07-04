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
        if recorded_success:
            raise
        if failure is None:
            failure = traceback.format_exc()

    if failure is not None:
        await _record_failure(session_factory, claimed, operation, failure)


def _as_run_output(output: Any) -> dict | None:
    return output if isinstance(output, dict) else None


async def _record_failure(
    session_factory: async_sessionmaker,
    claimed: ClaimedRun,
    operation: Operation | None,
    error: str,
) -> None:
    async with session_factory() as session, session.begin():
        await record_result(
            session,
            claimed,
            ExecutionResult(AttemptOutcome.failed, None, error),
            operation,
        )
