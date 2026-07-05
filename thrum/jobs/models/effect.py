"""The Effect model — an observed data mutation, keyed off the Attempt."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from thrum.jobs.models.base import Base
from thrum.jobs.models.enums import EffectKind


class Effect(Base):
    """One ``(table, kind)`` mutation an Operation made, with a row count.

    Recorded into the Attempt's own transaction so effects and the completion
    record commit atomically. Keyed on the Attempt, not the Run: a failed
    Execution rolls back and commits zero Effects. Table-level granularity, never
    per-row keys or per-value data.
    """

    __tablename__ = "effects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    attempt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("attempts.id"), index=True)
    kind: Mapped[EffectKind] = mapped_column(String(16))
    table_name: Mapped[str] = mapped_column(String(255))
    row_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC)
    )
