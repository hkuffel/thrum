"""Run one Operation inside one framework-owned transaction (ADR-0024).

Assemble the Operation's Capabilities from registered Providers on a shared
`AsyncExitStack`, inject them, run the body, then branch:

    success -> commit the succeeded Attempt, output, and observed Effects with
               the body's own DB writes as one atomic fact.
    failure -> roll all of it back, then record the failed Attempt + retry state
               in a separate transaction.

Worker death mid-execution raises `BaseException` (cancellation), not an
Operation failure: it propagates uncaught, nothing commits, and the Run stays
`running` with an open Attempt for the Reaper (ADR-0013).
"""

from __future__ import annotations

import inspect
import traceback
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING

from thrum.jobs.models import AttemptOutcome
from thrum.jobs.providers import Caller, ProviderContext
from thrum.jobs.serialization import SerializationContractError, ensure_serializable
from thrum.jobs.signature import ParamKind, classify
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
    """Execute one claimed Run in the Scope's transaction. A missing Operation, a
    raised body, or a non-serializable output becomes a `failed` Attempt recorded
    in a separate transaction — never propagated, so the Worker loop keeps
    turning."""
    operation = operations.get(claimed.operation_key)
    if operation is None:
        await _record_failure(
            session_factory,
            claimed,
            None,
            f"Operation {claimed.operation_key!r} is not registered in this Worker",
        )
        return

    capabilities = _capability_params(operation.fn, providers)
    failure: str | None = None
    try:
        async with session_factory() as session, AsyncExitStack() as stack:
            injected = {}
            ctx = ProviderContext(session=session)
            # catch a bad frozen Caller or a bad Provider `__aenter__` here and
            # record a failed Attempt before the stack unwinds. Propagating a error raise
            # here would mean the Reaper re-claims a bad row forever.
            try:
                caller = Caller.thaw(claimed.caller)
                for name, capability_type in capabilities:
                    injected[name] = await stack.enter_async_context(
                        providers[capability_type](ctx, caller)
                    )
                # Execution transaction nests inside the stack: Provider teardown
                # runs after commit on success, after rollback on a raised body.
                async with session.begin():
                    async with EffectRecorder.observing(session) as recorder:
                        output = await _invoke(operation.fn, claimed.inputs, injected)
                        # Flush the body's pending ORM writes through the recorder
                        # before the block detaches them; raw executes already fired.
                        await session.flush()
                    ensure_serializable(output, owner=claimed.operation_key, role="output")
                    await recorder.write(session, claimed.attempt_id)
                    await record_result(
                        session,
                        claimed,
                        ExecutionResult(AttemptOutcome.succeeded, output, None),
                        operation,
                    )
            except SerializationContractError as exc:
                # Record its message, not the traceback the generic handler below
                # would otherwise capture for this same exception.
                failure = str(exc)
            except Exception:
                failure = traceback.format_exc()
    except Exception:
        # Provider teardown raised during stack unwind. On success the Run already
        # committed `succeeded`, so propagate rather than overwrite it. On the
        # failure path nothing is recorded yet: swallow and record below.
        if failure is None:
            raise

    # Runs after the execution transaction rolled back and Providers tore down.
    if failure is not None:
        await _record_failure(session_factory, claimed, operation, failure)


async def _invoke(fn, inputs: dict, injected: dict) -> dict | None:
    """Call the body with inputs plus injected Capabilities, await if async. The
    output column holds a dict; a non-dict return is dropped."""
    result = fn(**inputs, **injected)
    if inspect.isawaitable(result):
        result = await result
    return result if isinstance(result, dict) else None


def _capability_params(fn, providers: dict[type, Provider]) -> list[tuple[str, type]]:
    """(param name, capability type) pairs to inject, resolved against the
    registered Provider types — the set Compile validated."""
    if not providers:
        return []
    model = classify(fn, capability_types=frozenset(providers))
    return [(p.name, p.annotation) for p in model.parameters if p.kind is ParamKind.CAPABILITY]


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
