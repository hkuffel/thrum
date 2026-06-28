"""The Execution Scope: run one Operation inside one framework-owned transaction
(ADR-0024).

The Scope owns the execution transaction ("Txn 2"). It assembles the Operation's
Capabilities via registered Providers on a shared `AsyncExitStack`, injects them
into the keyword-only capability params, runs the body, and branches:

    success  -> record the succeeded Attempt + output + the observed Effects in
                Txn 2, commit. The Operation's DB writes, the Effect records, and
                its completion record become one atomic fact.
    failure  -> roll back Txn 2 (nothing lands), then record the failed Attempt
                + retry state in a separate short transaction. The failure
                outcome must not ride Txn 2 or it would roll back with the
                doomed effects and the Run would hang `running` forever.

A worker death mid-Txn-2 raises `BaseException` (cancellation), which is not an
Operation-attributable failure: it propagates uncaught so nothing commits and the
Run stays `running` with an open Attempt for the Reaper to recover (ADR-0013).
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
    from thrum.jobs.registry import Task
    from thrum.jobs.worker.claim import ClaimedRun


async def run_scoped(
    session_factory: async_sessionmaker,
    claimed: ClaimedRun,
    operations: dict[str, Task],
    providers: dict[type, Provider],
) -> None:
    """Execute one claimed Run inside the Scope's transaction. An unresolved
    Operation, a raised body, and a non-serializable output all land as a
    `failed` Attempt recorded in a separate transaction — never propagated, so
    the Worker loop keeps turning."""
    operation = operations.get(claimed.operation_key)
    if operation is None:
        await _record_failure(
            session_factory,
            claimed,
            None,
            f"Operation {claimed.operation_key!r} is not registered in this Worker",
        )
        return

    caller = Caller.thaw(claimed.caller)
    capabilities = _capability_params(operation.fn, providers)
    failure: str | None = None
    try:
        async with session_factory() as session, AsyncExitStack() as stack:
            injected = {}
            ctx = ProviderContext(session=session)
            # The inner try covers Provider setup through the commit so a
            # Provider __aenter__ that raises is recorded as a failed Attempt,
            # not propagated. It ends before the stack unwinds because a teardown
            # that raises after a committed success must propagate, never be
            # re-recorded over the already-`succeeded` Run. Txn 2 nests inside
            # the stack so teardown runs after commit on success and after
            # rollback on a raised body.
            try:
                for name, capability_type in capabilities:
                    injected[name] = await stack.enter_async_context(
                        providers[capability_type](ctx, caller)
                    )
                async with session.begin():
                    async with EffectRecorder.observing(session) as recorder:
                        output = await _invoke(operation.fn, claimed.inputs, injected)
                        # Force the body's pending ORM writes through the recorder
                        # before the block exits and detaches; raw executes already
                        # fired.
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
                # A non-serializable output rolled Txn 2 back; fail the Attempt
                # with the contract error rather than a traceback (Run.output is
                # JSONB; a raise there would loop the same bad output forever).
                failure = str(exc)
            except Exception:
                failure = traceback.format_exc()
    except Exception:
        # A Provider teardown raised during stack unwind. On the success path
        # (failure is None) the Run already committed `succeeded` — let it
        # propagate rather than overwrite that record. On the failure path the
        # outcome is not yet recorded, so swallow it and record the failure
        # below, keeping the batch loop turning.
        if failure is None:
            raise

    # The failure-path second transaction runs after Txn 2 has rolled back and
    # the Providers are torn down — isolated, as ADR-0024 requires.
    if failure is not None:
        await _record_failure(session_factory, claimed, operation, failure)


async def _invoke(fn, inputs: dict, injected: dict) -> dict | None:
    """Call the Operation body with its inputs plus injected Capabilities,
    awaiting an async body. The JSONB output column holds a dict; a non-dict
    return is dropped."""
    result = fn(**inputs, **injected)
    if inspect.isawaitable(result):
        result = await result
    return result if isinstance(result, dict) else None


def _capability_params(fn, providers: dict[type, Provider]) -> list[tuple[str, type]]:
    """The (param name, capability type) pairs to inject, resolved against the
    registered Provider types — the injection set Compile validated."""
    if not providers:
        return []
    model = classify(fn, capability_types=frozenset(providers))
    return [
        (p.name, p.annotation)
        for p in model.parameters
        if p.kind is ParamKind.CAPABILITY
    ]


async def _record_failure(
    session_factory: async_sessionmaker,
    claimed: ClaimedRun,
    operation: Task | None,
    error: str,
) -> None:
    """The failure-path second transaction (today's `record.py`): close the
    Attempt `failed` and retry-or-terminal, isolated from the rolled-back Txn 2."""
    async with session_factory() as session, session.begin():
        await record_result(
            session,
            claimed,
            ExecutionResult(AttemptOutcome.failed, None, error),
            operation,
        )
