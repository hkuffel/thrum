"""Providers — how a Capability is constructed against the execution transaction
(ADR-0024).

A Provider is an async context manager taking the Execution Scope's transaction
context and the Caller on whose authority the Operation runs. It yields the
Capability object the Scope injects into the Operation's keyword-only param. The
transaction belongs to the Scope, not the Provider; a Provider only constructs an
effect-bearing object against it.

v1 ships exactly one real Provider, `db_provider`, which surfaces the Scope's
session — write-capable by default, write-blocking when the param declares
`ReadOnly[Session]` (ADR-0027). The Caller argument is present from day one — passed
a system-default Caller — so per-Caller attenuation slots in without a signature
change.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Protocol

from sqlalchemy import Delete, Insert, Update, event
from sqlalchemy.sql.elements import TextClause

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from contextlib import AbstractAsyncContextManager

    from sqlalchemy.engine import Connection
    from sqlalchemy.ext.asyncio import AsyncSession


class ReadOnly:
    """Attenuation marker declaring a capability param's least-authority ceiling
    (e.g. `db: ReadOnly[Session]`). `ReadOnly[T]` is `Annotated[T, ReadOnly]`,
    so the registered capability type stays `T`. The unmarked default is
    full, write-capable authority."""

    def __class_getitem__(cls, item: Any) -> Any:
        return Annotated[item, cls]


@dataclass(frozen=True)
class Caller:
    """The identity on whose authority an Operation runs. A Provider reads it to
    attenuate the Capability it constructs. No attenuation logic in v1.
    A system default flows the path that a real Caller will travel once
    auth and a second transport exist."""

    subject: str

    @classmethod
    def system(cls) -> Caller:
        return cls(subject="system")

    def freeze(self) -> dict[str, Any]:
        """Serialize the Caller onto a Run at Enqueue. The queue is the one
        transport that cannot supply a live Caller, so identity is frozen
        here and thawed back into the Scope at execution."""
        return {"subject": self.subject}

    @classmethod
    def thaw(cls, frozen: dict[str, Any] | None) -> Caller:
        """Restore the Caller frozen onto a Run. A Run with no frozen Caller
        runs on system authority."""
        if frozen is None:
            return cls.system()
        return cls(subject=frozen["subject"])


@dataclass
class ProviderContext:
    """What the Scope hands every Provider. Each Provider interprets the ones it
    understands."""

    session: AsyncSession
    attenuations: tuple[Any, ...] = ()


class Provider(Protocol):
    """Construct a Capability against the Scope's transaction for one execution.
    Entered and exited on the Scope's `AsyncExitStack`."""

    def __call__(
        self, ctx: ProviderContext, caller: Caller
    ) -> AbstractAsyncContextManager[Any]: ...


@asynccontextmanager
async def db_provider(ctx: ProviderContext, caller: Caller) -> AsyncIterator[AsyncSession]:
    """Surface the Scope's session as the `db` Capability. The session already
    records its effects into the Scope's transaction, so there is nothing to
    construct around it."""
    if ReadOnly not in ctx.attenuations:
        yield ctx.session
        return
    sync_conn = (await ctx.session.connection()).sync_connection
    event.listen(sync_conn, "before_execute", _reject_write)
    try:
        yield ctx.session
    finally:
        event.remove(sync_conn, "before_execute", _reject_write)


# Textual writes: recover the verb from the SQL prefix, past leading comments.
# Covers DML (INSERT/UPDATE/DELETE), whole-table wipes (TRUNCATE), and schema
# mutation (DDL) — none of which the ORM DML classes catch when issued via text().
_TEXT_WRITE = re.compile(
    r"^\s*(?:--[^\n]*\n|/\*.*?\*/\s*)*"
    r"(INSERT|UPDATE|DELETE|TRUNCATE|MERGE|DROP|CREATE|ALTER|GRANT|REVOKE)\b",
    re.IGNORECASE | re.DOTALL,
)


def _reject_write(
    conn: Connection, clauseelement: Any, multiparams: Any, params: Any, execution_options: Any
) -> None:
    if isinstance(clauseelement, (Insert, Update, Delete)) or (
        isinstance(clauseelement, TextClause) and _TEXT_WRITE.match(clauseelement.text)
    ):
        raise PermissionError(
            "read-only capability: this Operation declared ReadOnly[Session] and may "
            "not issue writes (INSERT/UPDATE/DELETE/TRUNCATE or DDL)"
        )
