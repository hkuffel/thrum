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
    def __class_getitem__(cls, item: Any) -> Any:
        return Annotated[item, cls]


@dataclass(frozen=True)
class Caller:
    subject: str

    @classmethod
    def system(cls) -> Caller:
        return cls(subject="system")

    def freeze(self) -> dict[str, Any]:
        return {"subject": self.subject}

    @classmethod
    def thaw(cls, frozen: dict[str, Any] | None) -> Caller:
        if frozen is None:
            return cls.system()
        return cls(subject=frozen["subject"])


@dataclass
class ProviderContext:
    session: AsyncSession
    attenuations: tuple[Any, ...] = ()


class Provider(Protocol):
    def __call__(
        self, ctx: ProviderContext, caller: Caller
    ) -> AbstractAsyncContextManager[Any]: ...


@asynccontextmanager
async def db_provider(ctx: ProviderContext, caller: Caller) -> AsyncIterator[AsyncSession]:
    if ReadOnly not in ctx.attenuations:
        yield ctx.session
        return
    sync_conn = (await ctx.session.connection()).sync_connection
    event.listen(sync_conn, "before_execute", _reject_write)
    try:
        yield ctx.session
    finally:
        event.remove(sync_conn, "before_execute", _reject_write)


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
