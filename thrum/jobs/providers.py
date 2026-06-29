"""Providers — how a Capability is constructed against the execution transaction
(ADR-0024).

A Provider is an async context manager taking the Execution Scope's transaction
context and the Caller on whose authority the Operation runs. It yields the
Capability object the Scope injects into the Operation's keyword-only param. The
transaction belongs to the Scope, not the Provider; a Provider only constructs an
effect-bearing object against it.

v1 ships exactly one real Provider, `db_provider`, which surfaces the Scope's
session directly. The Caller argument is present from day one — passed a
system-default Caller — so attenuation and the freeze/thaw path slot in without a
signature change.

SDK-core surface: stdlib only, no Worker/server imports (the import-discipline
law)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from contextlib import AbstractAsyncContextManager

    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class Caller:
    """The identity on whose authority an Operation runs. A Provider reads it to
    attenuate the Capability it constructs (read-only db, tenant-scoped db,
    amount-capped payments). v1 builds no attenuation logic — only a system
    default flows the path that a real Caller will travel once auth and a second
    transport exist."""

    subject: str

    @classmethod
    def system(cls) -> Caller:
        return cls(subject="system")

    def freeze(self) -> dict[str, Any]:
        """Serialize the Caller onto a Run at Enqueue. The queue is the one
        transport that cannot supply a live Caller — invocation and execution are
        separated in time and process — so identity is frozen here and thawed back
        into the Scope at execution (ADR-0024)."""
        return {"subject": self.subject}

    @classmethod
    def thaw(cls, frozen: dict[str, Any] | None) -> Caller:
        """Restore the Caller frozen onto a Run. A Run with no frozen Caller — a
        cron occurrence the scheduler materialized — runs on system authority."""
        if frozen is None:
            return cls.system()
        return cls(subject=frozen["subject"])


@dataclass
class ProviderContext:
    """What the Scope hands every Provider: the one execution transaction's
    session. A Provider constructs its Capability against this."""

    session: AsyncSession


class Provider(Protocol):
    """Construct a Capability against the Scope's transaction for one execution.
    Entered and exited on the Scope's `AsyncExitStack`."""

    def __call__(
        self, ctx: ProviderContext, caller: Caller
    ) -> AbstractAsyncContextManager[Any]: ...


@asynccontextmanager
async def db_provider(ctx: ProviderContext, caller: Caller) -> AsyncIterator[AsyncSession]:
    """Surface the Scope's session as the `db` Capability. The most direct
    Provider: the session already records its effects into the Scope's
    transaction, so there is nothing to construct around it."""
    yield ctx.session
