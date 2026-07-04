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
        UniqueConstraint("schedule_id", "fire_time", name="uq_runs_schedule_fire_time"),
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
