"""The Schedule: a recurring rule that creates Runs (CONTEXT.md). Stores the
durable intent as cron + IANA timezone — never precomputed far-future UTC
(ADR-0015). Declares the expectation policy that materialization snapshots
onto each Run.

Two-gate pause model (ADR-0022): a Schedule materializes only when both gates
are active. The declaration gate is code-owned (written by reconcile); the
operational gate is control-plane-owned (reserved in v1, written by a v2
dashboard/agent pause). Collapsing both into one boolean would make every
Worker restart silently revive an operator's pause."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, Interval, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from thrum.jobs.models.base import Base


class Schedule(Base):
    __tablename__ = "schedules"
    __table_args__ = (
        # Schedule identity is (operation, cron): one operation may declare
        # several recurrences (e.g. a nightly + a Monday-morning run), each a
        # distinct row. Including `cron` lets the reconcile upsert update a
        # re-declared recurrence in place while still inserting sibling schedules
        # instead of collapsing them onto the last-declared one.
        UniqueConstraint(
            "operation_namespace", "operation_name", "cron",
            name="uq_schedules_operation_cron",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # Target Operation identity (namespace.name) — user-owned, stable across refactors.
    operation_namespace: Mapped[str] = mapped_column(String(255))
    operation_name: Mapped[str] = mapped_column(String(255))

    # Recurrence intent.
    cron: Mapped[str] = mapped_column(String(255))
    timezone: Mapped[str] = mapped_column(String(64))  # IANA name, e.g. "America/Vancouver"

    # Expectation policy — snapshotted onto each materialized Run.
    declared_duration: Mapped[dt.timedelta | None] = mapped_column(Interval, nullable=True)
    sla: Mapped[dt.timedelta | None] = mapped_column(Interval, nullable=True)
    start_grace: Mapped[dt.timedelta | None] = mapped_column(Interval, nullable=True)

    # Two-gate pause model (ADR-0022).
    # Declaration gate: code-owned, written by reconcile only.
    declaration_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_declared_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Operational gate: control-plane-owned, reserved in v1 (never written by
    # reconcile). null = active; a v2 pause sets a timestamp + actor.
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
        """Both gates must be active for the Schedule to materialize."""
        return self.declaration_active and self.operationally_paused_at is None
