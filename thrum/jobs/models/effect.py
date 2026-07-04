"""The Effect: what an Attempt committed to the database, observed by
instrumenting the Scope's session and written into the execution transaction (ADR-0024).

One row per (table, kind) per Attempt with a `row_count` — table-level, never
per-row: a 10k-row batch update is one Effect with `row_count = 10000`, not 10k
rows. Effects key off the Attempt: a failed Attempt's writes roll back with the
execution transaction and contribute zero Effects, so a retry's Effects attribute to the retry.

The derived Zero-Effect flag (`succeeded` with zero Effects) is computed from the
Effect count, never stored as a `status` — observability must not mutate lifecycle
state.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from thrum.jobs.models.base import Base
from thrum.jobs.models.enums import EffectKind


class Effect(Base):
    __tablename__ = "effects"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    attempt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("attempts.id"), index=True)
    kind: Mapped[EffectKind] = mapped_column(String(16))
    table_name: Mapped[str] = mapped_column(String(255))
    row_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC)
    )
