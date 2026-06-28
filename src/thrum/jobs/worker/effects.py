"""The Effect recorder: observe what an Operation committed and record it as
table-level Effect rows in Txn 2 (ADR-0024).

The recorder listens on the Scope's execution connection for Core DML
(`after_execute`), aggregating every mutation to one `(table, kind)` entry with a
summed `row_count`. A 10k-row batch update is one entry with `row_count = 10000`,
not 10k — the count comes from the cursor's rowcount, not from per-row events,
which is also why a pure ORM `before_flush` listener cannot serve here.

It is best-effort and isolated. Sharing the operation's transaction makes a
listener that raises dangerous — it could abort Txn 2 and roll back real effects —
so every observation is swallowed, and the Effect rows are written inside a
SAVEPOINT so a failed write cannot doom the operation's commit. What is
load-bearing is the atomicity of the records that do land, not the recording's
reliability.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from sqlalchemy import Delete, Insert, Update, event, func, select
from sqlalchemy.sql.elements import TextClause

from thrum.jobs.models import Attempt, AttemptOutcome, Effect, EffectKind

if TYPE_CHECKING:
    import uuid

    from sqlalchemy import Connection
    from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)

# Raw textual DML (the tests' `text("INSERT ...")`, hand-written statements) carries
# no parse tree, so the verb and target are recovered from the SQL prefix.
_TEXT_DML = (
    (EffectKind.insert, re.compile(r"^\s*INSERT\s+INTO\s+([^\s(]+)", re.IGNORECASE)),
    (EffectKind.update, re.compile(r"^\s*UPDATE\s+(?:ONLY\s+)?([^\s(]+)", re.IGNORECASE)),
    (EffectKind.delete, re.compile(r"^\s*DELETE\s+FROM\s+(?:ONLY\s+)?([^\s(]+)", re.IGNORECASE)),
)


class EffectRecorder:
    """Accumulates an execution's mutations while attached to its connection, then
    writes them as one Effect row per (table, kind) for the Attempt."""

    def __init__(self) -> None:
        self._counts: dict[tuple[str, EffectKind], int] = {}
        self._conn: Connection | None = None

    def attach(self, sync_conn: Connection) -> None:
        """Listen on the execution connection. Idempotently swallows every
        observation so a parse or listener fault can never abort Txn 2."""
        self._conn = sync_conn
        event.listen(sync_conn, "after_execute", self._after_execute)

    def detach(self) -> None:
        """Stop listening so the framework's own writes (Effect rows, the Attempt
        and Run records) are never counted as the Operation's Effects."""
        if self._conn is not None:
            event.remove(self._conn, "after_execute", self._after_execute)
            self._conn = None

    def _after_execute(self, conn, clauseelement, multiparams, params, execution_options, result):
        try:
            classified = _classify(clauseelement)
            if classified is None:
                return
            kind, table = classified
            rows = result.rowcount
            if rows is None or rows < 0:
                return
            self._counts[(table, kind)] = self._counts.get((table, kind), 0) + rows
        except Exception:
            log.exception("Effect recorder failed to observe a statement; skipping it")

    async def write(self, session: AsyncSession, attempt_id: uuid.UUID) -> None:
        """Write the aggregated Effects into Txn 2 inside a SAVEPOINT, so a write
        failure rolls back only the Effect rows, never the operation's effects."""
        if not self._counts:
            return
        try:
            async with session.begin_nested():
                session.add_all(
                    Effect(
                        attempt_id=attempt_id,
                        table_name=table,
                        kind=kind,
                        row_count=rows,
                    )
                    for (table, kind), rows in self._counts.items()
                )
        except Exception:
            log.exception("Effect recorder failed to write Effects; the operation still commits")


def _classify(clauseelement) -> tuple[EffectKind, str] | None:
    """The (kind, table) a DML statement mutates, or None for non-DML (selects,
    DDL) and unparseable text."""
    if isinstance(clauseelement, Insert):
        return EffectKind.insert, clauseelement.table.name
    if isinstance(clauseelement, Update):
        return EffectKind.update, clauseelement.table.name
    if isinstance(clauseelement, Delete):
        return EffectKind.delete, clauseelement.table.name
    if isinstance(clauseelement, TextClause):
        for kind, pattern in _TEXT_DML:
            match = pattern.match(clauseelement.text)
            if match is not None:
                return kind, _bare_name(match.group(1))
    return None


def _bare_name(token: str) -> str:
    """The table name without a schema qualifier or quoting — `"thrum"."runs"`
    and `public.foo` both reduce to the trailing identifier."""
    return token.rsplit(".", 1)[-1].strip('"')


async def is_zero_effect(session: AsyncSession, attempt_id: uuid.UUID) -> bool:
    """The derived Zero-Effect flag: a `succeeded` Attempt that committed no
    Effects. Computed from the Effect count, never stored as a status (ADR-0024) —
    a legitimate no-op stays `succeeded` and is merely flagged."""
    attempt = await session.get(Attempt, attempt_id)
    if attempt is None or attempt.outcome != AttemptOutcome.succeeded:
        return False
    count = (
        await session.execute(
            select(func.count()).select_from(Effect).where(Effect.attempt_id == attempt_id)
        )
    ).scalar_one()
    return count == 0
