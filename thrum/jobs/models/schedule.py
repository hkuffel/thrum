from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, Interval, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from thrum.jobs.models.base import Base


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (
        UniqueConstraint(
            "operation_namespace",
            "operation_name",
            "cron",
            name="uq_schedules_operation_cron",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    operation_namespace: Mapped[str] = mapped_column(String(255))
    operation_name: Mapped[str] = mapped_column(String(255))

    cron: Mapped[str] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64))

    declared_duration: Mapped[dt.timedelta | None] = mapped_column(Interval, nullable=True)
    sla: Mapped[dt.timedelta | None] = mapped_column(Interval, nullable=True)
    start_grace: Mapped[dt.timedelta | None] = mapped_column(Interval, nullable=True)

    declaration_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_declared_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    operationally_paused_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    operationally_paused_by: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @property
    def is_active(self) -> bool:
        return self.declaration_active and self.operationally_paused_at is None
