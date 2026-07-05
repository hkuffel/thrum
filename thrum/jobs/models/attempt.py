"""The Attempt model — one try at executing a Run, and where the Lease lives."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from thrum.jobs.models.base import Base
from thrum.jobs.models.enums import AttemptOutcome


class Attempt(Base):
    """One try at executing a Run; a retry adds an Attempt, not a new Run.

    The Attempt is the durable Execution — Effects key off it, not the Run — and
    it carries the Lease. An open Attempt (``ended_at IS NULL``) with an expired
    Lease is an orphan the Reaper reclaims.

    Attributes:
        claimed_by: The Worker holding the Lease.
        lease_expires_at: Lease deadline, renewed by the Heartbeat while running.
        ended_at: When the Attempt closed; ``None`` while it is still open.
        outcome: The terminal outcome, set when the Attempt ends.
        error: Failure detail, when the outcome is a failure.
    """

    __tablename__ = "attempts"
    __table_args__ = (UniqueConstraint("run_id", "attempt_number", name="uq_attempts_run_number"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)

    claimed_by: Mapped[str] = mapped_column(String(128))
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)

    ended_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[AttemptOutcome | None] = mapped_column(String(16), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
