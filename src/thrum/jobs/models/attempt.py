"""The Attempt: one try at executing a Run (ADR-0002). Append-only — the
immutable per-try ledger. The Lease lives HERE, not on the Run (core-loop Q2 /
ADR-0013): an orphan is just an open Attempt with an expired lease."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from thrum.jobs.models.base import Base
from thrum.jobs.models.enums import AttemptOutcome


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (
        UniqueConstraint("run_id", "attempt_number", name="uq_attempts_run_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)

    # Lease / liveness (ADR-0013). The claim txn stamps claimed_by + started_at +
    # lease_expires_at; the Worker heartbeats lease_expires_at while executing.
    # Reaper predicate: ended_at IS NULL AND lease_expires_at < now().
    claimed_by: Mapped[str] = mapped_column(String(128))  # Worker id
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)

    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[AttemptOutcome | None] = mapped_column(String(16), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)  # traceback
