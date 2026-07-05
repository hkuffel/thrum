"""The Run model — a single durable execution of an Operation."""

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
    """A single durable execution of an Operation with a status lifecycle.

    A Run is created by one of three triggers — a Schedule, an Enqueue call, or
    a workflow step — and moves through ``pending → running → succeeded/failed``.
    It is the row the dashboard shows, the unit Dispatch claims, and the parent
    of 1..N Attempts (a retry adds an Attempt, never a new Run).

    Attributes:
        schedule_id: The owning Schedule, or ``None`` for an ad-hoc Enqueue.
        trigger: Which of the three triggers created this Run.
        fire_time: The logical identity of a scheduled occurrence ("Tuesday's
            02:00 run"); ``None`` for ad-hoc Runs.
        next_attempt_at: When the Run next becomes claimable — set ahead for a
            retry backoff.
        caller: The authenticated identity frozen onto the Run at Enqueue and
            thawed into the Execution Scope when the Worker runs it.
        max_attempts: The retry budget snapshotted from the Operation, or
            ``None`` to defer to the executing Worker's default.
        expected_start_at: The Expectation snapshot — when the Run should start,
            captured immutably at materialization and never read live.
        expected_finish_by: The Expectation deadline the Overrun flag measures
            against.
        expected_duration: The expected wall-clock duration from the Schedule's
            policy.
        created_version: The Operation Fingerprint stamped at creation. Stored
            in v1 though nothing reads it until v2, because code is never
            persisted and so cannot be recomputed retroactively.
    """

    __tablename__ = "runs"
    __table_args__ = (
        # (schedule_id, fire_time) is an occurrence's identity: the uniqueness
        # is what makes the materialization sweep idempotent and crash-safe.
        UniqueConstraint("schedule_id", "fire_time", name="uq_runs_schedule_fire_time"),
        # Covers the Dispatch query, which selects claimable Runs by status and
        # next_attempt_at, ordered by fire_time.
        Index("ix_runs_claimable", "status", "next_attempt_at", "fire_time"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    schedule_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("schedules.id"), nullable=True)

    operation_namespace: Mapped[str] = mapped_column(String(255))
    operation_name: Mapped[str] = mapped_column(String(255))
    trigger: Mapped[Trigger] = mapped_column(String(16))

    status: Mapped[RunStatus] = mapped_column(String(16), default=RunStatus.pending, index=True)

    fire_time: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    inputs: Mapped[dict] = mapped_column(JSONB, default=dict)
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    caller: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    max_attempts: Mapped[int | None] = mapped_column(nullable=True)

    expected_start_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expected_finish_by: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expected_duration: Mapped[dt.timedelta | None] = mapped_column(Interval, nullable=True)

    created_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.UTC)
    )
