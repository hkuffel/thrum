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
from collections import Counter
from contextlib import asynccontextmanager
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
    (EffectKind.insert, re.compile(r"^INSERT\s+INTO\s+([^\s(]+)", re.IGNORECASE)),
    (EffectKind.update, re.compile(r"^UPDATE\s+(?:ONLY\s+)?([^\s(]+)", re.IGNORECASE)),
    (EffectKind.delete, re.compile(r"^DELETE\s+FROM\s+(?:ONLY\s+)?([^\s(]+)", re.IGNORECASE)),
)

# Leading whitespace and SQL comments before the verb, so `-- note\nINSERT …` and
# `/* hint */ UPDATE …` still classify rather than silently miss.
_LEADING_NOISE = re.compile(r"^(?:\s+|--[^\n]*|/\*.*?\*/)+", re.DOTALL)


class EffectRecorder:
    """Accumulates an execution's mutations while attached to its connection, then
    writes them as one Effect row per (table, kind) for the Attempt."""

    def __init__(self) -> None:
        self._counts: Counter[tuple[str, EffectKind]] = Counter()
        self._conn: Connection | None = None

    @classmethod
    @asynccontextmanager
    async def observing(cls, session: AsyncSession):
        """Record mutations on `session`'s connection for the duration of the
        block, detaching on exit. Exit must precede the framework's own writes
        (the Effect rows, the Attempt and Run records) so they are never counted
        as the Operation's Effects — the `with` scope makes that ordering
        structural."""
        recorder = cls()
        recorder._attach((await session.connection()).sync_connection)
        try:
            yield recorder
        finally:
            recorder._detach()

    def _attach(self, sync_conn: Connection) -> None:
        self._conn = sync_conn
        event.listen(sync_conn, "after_execute", self._after_execute)

    def _detach(self) -> None:
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
            self._counts[(table, kind)] += rows
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
        sql = _LEADING_NOISE.sub("", clauseelement.text)
        for kind, pattern in _TEXT_DML:
            match = pattern.match(sql)
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
    a legitimate no-op stays `succeeded` and is merely flagged.

    One outer-joined query, so the result reflects committed state rather than a
    possibly-stale Attempt cached in the session's identity map."""
    row = (
        await session.execute(
            select(Attempt.outcome, func.count(Effect.id))
            .outerjoin(Effect, Effect.attempt_id == Attempt.id)
            .where(Attempt.id == attempt_id)
            .group_by(Attempt.outcome)
        )
    ).first()
    if row is None:
        return False
    outcome, count = row
    return outcome == AttemptOutcome.succeeded and count == 0
