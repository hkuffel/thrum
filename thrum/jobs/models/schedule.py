"""The Schedule model — a recurring rule that materializes Runs on a cadence."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, Interval, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from thrum.jobs.models.base import Base


class Schedule(Base):
    """The durable form of a code-declared recurring trigger.

    Stores the cron + IANA timezone intent — never precomputed future instants —
    plus the Expectation policy the SLO engine measures against. Active state has
    two independent axes: whether the code still declares it
    (``declaration_active``) and whether an operator has paused it
    (``operationally_paused_at``); it materializes Runs only when both allow.

    Attributes:
        declaration_active: False once the Operation stops declaring this cron.
        last_declared_at: When the declaration was last seen at startup.
        operationally_paused_at: When an operator paused it, if paused.
        operationally_paused_by: Who paused it.
    """

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
        """Whether the Schedule should materialize Runs — declared and not paused."""
        return self.declaration_active and self.operationally_paused_at is None
