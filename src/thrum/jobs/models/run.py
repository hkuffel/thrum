"""The Run: the central primitive. The row mutates as it loops
scheduled/pending -> running -> pending -> ... while immutable Attempts accrue.
Per-try data lives on Attempt, never here.

Claimability predicate (the dispatch query, ADR-0008):
    (status = 'pending'   AND next_attempt_at <= now())   -- enqueues + retries
    OR (status = 'scheduled' AND fire_time      <= now())  -- cron occurrences
All time comparisons evaluate DB-side against Postgres now().
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Interval,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from thrum.jobs.models.base import Base
from thrum.jobs.models.enums import RunStatus, Trigger


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (
        # Materialization idempotency + crash-safety (ADR-0003): fire_time is the
        # logical identity of a cron occurrence. INSERT ... ON CONFLICT DO NOTHING.
        UniqueConstraint("schedule_id", "fire_time", name="uq_runs_schedule_fire_time"),
        Index("ix_runs_claimable", "status", "next_attempt_at", "fire_time"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # nullable: ad-hoc enqueues and (v2) workflow steps have no Schedule.
    schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("schedules.id"), nullable=True
    )
    # A nullable workflow_run_id FK is a trivial additive v2 migration with zero
    # backfill — deliberately omitted now, not foreclosed.

    operation_namespace: Mapped[str] = mapped_column(String(255))
    operation_name: Mapped[str] = mapped_column(String(255))
    trigger: Mapped[Trigger] = mapped_column(String(16))

    status: Mapped[RunStatus] = mapped_column(String(16), default=RunStatus.pending, index=True)

    # Dispatch gating timestamps (timestamptz, UTC).
    fire_time: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    inputs: Mapped[dict] = mapped_column(JSONB, default=dict)  # jsonb; store IDs, not objects
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Per-call retries override: NULL means inherit from the Operation's default.
    # Set by op.enqueue(..., retries=N) as N+1 (attempt budget). The queue
    # projection is the only durable projection that writes this; non-durable
    # projections (HTTP) must not set it.
    max_attempts: Mapped[int | None] = mapped_column(nullable=True)

    # Expectation snapshot — captured immutably at materialization. late/overrun are
    # derived from these + Attempt actuals, not stored as status.
    expected_start_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expected_finish_by: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expected_duration: Mapped[dt.timedelta | None] = mapped_column(Interval, nullable=True)

    # Best-effort version-aware dispatch seam.
    created_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC)
    )
