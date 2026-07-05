"""The Execution Scope — the framework-owned context that runs one Execution.

The scope opens the single execution transaction, constructs each Capability
against it via its Provider, invokes the Operation body, and — on the caller's
behalf, not any Provider's — commits, so the Operation's effects and "the job
finished" land as one atomic fact.
"""

from __future__ import annotations

import functools
import inspect
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from thrum.jobs.providers import ProviderContext
from thrum.jobs.serialization import ensure_serializable
from thrum.jobs.signature import ParamKind, classify

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from thrum.jobs.providers import Caller, Provider
    from thrum.jobs.registry import Operation


@dataclass
class BoundExecution:
    """An Operation with its Capabilities injected, ready to run once.

    Attributes:
        session: The scope's execution transaction session.
        operation: The Operation whose body will be invoked.
        injected: Constructed Capabilities keyed by parameter name.
    """

    session: AsyncSession
    operation: Operation
    injected: dict[str, Any]

    async def run(self, inputs: dict) -> Any:
        """Invoke the Operation body and return its output.

        Awaits the result if the Operation is async, then flushes so any writes
        reach the transaction before it is committed by the enclosing scope.

        Raises:
            SerializationContractError: If the output cannot cross a transport's
                serialization boundary as JSON.
        """
        result = self.operation.fn(**inputs, **self.injected)
        if inspect.isawaitable(result):
            result = await result
        await self.session.flush()
        ensure_serializable(result, owner=self.operation.key, role="output")
        return result


class ExecutionScope:
    """Factory for Executions bound to a fresh transaction and Capabilities."""

    def __init__(
        self, session_factory: async_sessionmaker, providers: dict[type, Provider]
    ) -> None:
        self._session_factory = session_factory
        self._providers = providers

    @asynccontextmanager
    async def open(self, operation: Operation, caller: Caller) -> AsyncIterator[BoundExecution]:
        """Open one execution transaction with Capabilities constructed against it.

        Every Provider is entered on a shared ``AsyncExitStack`` so that the
        stack unwinds — and every Capability tears down — deterministically on
        both success and failure, and the transaction commits when the ``with``
        block exits cleanly.

        Yields:
            The bound Execution to run inside the open transaction.
        """
        capabilities = _capability_params(operation.fn, frozenset(self._providers))
        async with self._session_factory() as session:
            async with AsyncExitStack() as stack, session.begin():
                injected: dict[str, Any] = {}
                for name, capability_type, attenuations in capabilities:
                    ctx = ProviderContext(session=session, attenuations=attenuations)
                    injected[name] = await stack.enter_async_context(
                        self._providers[capability_type](ctx, caller)
                    )
                yield BoundExecution(session, operation, injected)

    async def invoke(self, operation: Operation, inputs: dict, caller: Caller) -> Any:
        """Run an Operation to completion in a fresh scope and return its output."""
        async with self.open(operation, caller) as bound:
            return await bound.run(inputs)


@functools.cache
def _capability_params(
    fn: Callable[..., Any], capability_types: frozenset[type]
) -> tuple[tuple[str, type, tuple[Any, ...]], ...]:
    """Extract the Capability params of an Operation, cached per function.

    Returns:
        A tuple of ``(name, capability_type, attenuations)`` per Capability
        parameter, in declaration order.
    """
    if not capability_types:
        return ()
    model = classify(fn, capability_types=capability_types)
    return tuple(
        (p.name, p.annotation, p.metadata)
        for p in model.parameters
        if p.kind is ParamKind.CAPABILITY
    )
