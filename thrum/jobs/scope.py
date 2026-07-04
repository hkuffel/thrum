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
    session: AsyncSession
    operation: Operation
    injected: dict[str, Any]

    async def run(self, inputs: dict) -> Any:
        result = self.operation.fn(**inputs, **self.injected)
        if inspect.isawaitable(result):
            result = await result
        await self.session.flush()
        ensure_serializable(result, owner=self.operation.key, role="output")
        return result


class ExecutionScope:
    def __init__(
        self, session_factory: async_sessionmaker, providers: dict[type, Provider]
    ) -> None:
        self._session_factory = session_factory
        self._providers = providers

    @asynccontextmanager
    async def open(self, operation: Operation, caller: Caller) -> AsyncIterator[BoundExecution]:
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
        async with self.open(operation, caller) as bound:
            return await bound.run(inputs)


@functools.cache
def _capability_params(
    fn: Callable[..., Any], capability_types: frozenset[type]
) -> tuple[tuple[str, type, tuple[Any, ...]], ...]:
    if not capability_types:
        return ()
    model = classify(fn, capability_types=capability_types)
    return tuple(
        (p.name, p.annotation, p.metadata)
        for p in model.parameters
        if p.kind is ParamKind.CAPABILITY
    )
