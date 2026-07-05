"""Capability construction — Providers, the Caller, and read-only Attenuation.

A Provider builds one Capability per Execution against the scope's transaction.
v1 ships exactly one, ``db_provider``, which surfaces the scope's session and
enforces the ``ReadOnly`` Attenuation marker.
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
    """Attenuation marker declaring a Capability param write-blocked.

    Written on the param as ``db: ReadOnly[Session]``, which is
    ``Annotated[Session, ReadOnly]`` — so the capability type stays ``Session``
    and the Data/Capability discriminator is untouched; the marker is read
    separately to attenuate.
    """

    def __class_getitem__(cls, item: Any) -> Any:
        return Annotated[item, cls]


@dataclass(frozen=True)
class Caller:
    """The authenticated identity an Operation runs on behalf of.

    Because the queue separates invocation from execution in time and process,
    the Caller is frozen onto the Run at Enqueue and thawed back into the scope
    at execution. v1 carries only a ``system`` subject through that real path.
    """

    subject: str

    @classmethod
    def system(cls) -> Caller:
        """The default identity used where no auth system supplies one yet."""
        return cls(subject="system")

    def freeze(self) -> dict[str, Any]:
        """Reduce to the JSON form stored on the Run at Enqueue."""
        return {"subject": self.subject}

    @classmethod
    def thaw(cls, frozen: dict[str, Any] | None) -> Caller:
        """Reconstruct from a Run's frozen form, defaulting to ``system``."""
        if frozen is None:
            return cls.system()
        return cls(subject=frozen["subject"])


@dataclass
class ProviderContext:
    """What a Provider builds its Capability against.

    Attributes:
        session: The scope's execution transaction; a Provider records into it
            but does not own or commit it.
        attenuations: The ``Annotated`` markers peeled off the param, narrowing
            the Capability's authority.
    """

    session: AsyncSession
    attenuations: tuple[Any, ...] = ()


class Provider(Protocol):
    """A per-Capability factory: an async context manager yielding one instance."""

    def __call__(
        self, ctx: ProviderContext, caller: Caller
    ) -> AbstractAsyncContextManager[Any]: ...


@asynccontextmanager
async def db_provider(ctx: ProviderContext, caller: Caller) -> AsyncIterator[AsyncSession]:
    """Provide the ``db`` Capability — the scope's session, write-gated on ReadOnly.

    Without the ``ReadOnly`` marker the session is yielded directly. With it, a
    ``before_execute`` listener is attached for the Capability's lifetime that
    rejects any write, then removed on exit so the gate never outlives this
    Operation's use of the shared connection.
    """
    if ReadOnly not in ctx.attenuations:
        yield ctx.session
        return
    sync_conn = (await ctx.session.connection()).sync_connection
    event.listen(sync_conn, "before_execute", _reject_write)
    try:
        yield ctx.session
    finally:
        event.remove(sync_conn, "before_execute", _reject_write)


# Matches a leading write verb after optional SQL comments, to gate raw text
# statements that bypass the Insert/Update/Delete construct check below.
_TEXT_WRITE = re.compile(
    r"^\s*(?:--[^\n]*\n|/\*.*?\*/\s*)*"
    r"(INSERT|UPDATE|DELETE|TRUNCATE|MERGE|DROP|CREATE|ALTER|GRANT|REVOKE)\b",
    re.IGNORECASE | re.DOTALL,
)


def _reject_write(
    conn: Connection, clauseelement: Any, multiparams: Any, params: Any, execution_options: Any
) -> None:
    """Raise if a statement mutates, enforcing the ``ReadOnly`` Attenuation.

    Raises:
        PermissionError: If the statement is a DML construct or raw text write.
    """
    if isinstance(clauseelement, (Insert, Update, Delete)) or (
        isinstance(clauseelement, TextClause) and _TEXT_WRITE.match(clauseelement.text)
    ):
        raise PermissionError(
            "read-only capability: this Operation declared ReadOnly[Session] and may "
            "not issue writes (INSERT/UPDATE/DELETE/TRUNCATE or DDL)"
        )
