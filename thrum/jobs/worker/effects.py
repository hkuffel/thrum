"""Effect recording — observing an Execution's data mutations.

The recorder instruments the framework-owned session, counting rows per
``(table, kind)``, and writes one Effect per pair into the Operation's own
transaction. Observation is best-effort and isolated: a recorder that raised
could roll back real effects, so every observation and write is guarded.
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

# Raw-text DML this recorder can attribute to a table, for statements issued as
# text rather than ORM constructs.
_TEXT_DML = (
    (EffectKind.insert, re.compile(r"^INSERT\s+INTO\s+([^\s(]+)", re.IGNORECASE)),
    (EffectKind.update, re.compile(r"^UPDATE\s+(?:ONLY\s+)?([^\s(]+)", re.IGNORECASE)),
    (EffectKind.delete, re.compile(r"^DELETE\s+FROM\s+(?:ONLY\s+)?([^\s(]+)", re.IGNORECASE)),
)

_LEADING_NOISE = re.compile(r"^(?:\s+|--[^\n]*|/\*.*?\*/)+", re.DOTALL)


class EffectRecorder:
    """Tallies DML on a session for the duration of one Execution."""

    def __init__(self) -> None:
        self._counts: Counter[tuple[str, EffectKind]] = Counter()
        self._conn: Connection | None = None

    @classmethod
    @asynccontextmanager
    async def observing(cls, session: AsyncSession):
        """Record every mutation on ``session`` while the context is open.

        Yields:
            The recorder; call ``write`` before the transaction commits to
            persist the tally.
        """
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
        # Isolation: a raise here would abort the Operation's own transaction, so
        # a failed observation is logged and dropped, never propagated.
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
        """Persist the tallied Effects, one row per ``(table, kind)``.

        Written in a savepoint and guarded so a recording failure rolls back
        only the Effect rows, never the Operation's real writes.
        """
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
    """Identify the mutation kind and target table of a statement, if any.

    Returns:
        A ``(kind, table)`` pair, or ``None`` if the statement is not a
        recognized write.
    """
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
    """Strip a schema qualifier and quotes from a table token."""
    return token.rsplit(".", 1)[-1].strip('"')


async def is_zero_effect(session: AsyncSession, attempt_id: uuid.UUID) -> bool:
    """Whether an Attempt succeeded yet committed no Effects — the wedge signal.

    Returns:
        True only when the Attempt's outcome is ``succeeded`` and it recorded
        zero Effects; False if it failed or recorded any.
    """
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
