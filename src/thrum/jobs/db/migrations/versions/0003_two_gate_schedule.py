"""two-gate schedule pause model (ADR-0022)

Replace the single `paused` boolean with two independent gates: the
declaration gate (code-owned, written by reconcile) and the operational gate
(control-plane-owned, reserved in v1). Add `last_declared_at` and the
`uq_schedules_task` unique constraint on (task_namespace, task_name).

Fresh databases already get the new columns from 0001's model-derived
`create_all`, so each operation is guarded for idempotency.

Revision ID: 0003_two_gate
Revises: 0002_start_grace
Create Date: 2026-06-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from thrum.jobs.models.base import SCHEMA

revision = "0003_two_gate"
down_revision = "0002_start_grace"
branch_labels = None
depends_on = None


def _has_column(bind, column: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = 'schedules' "
                "AND column_name = :column"
            ),
            {"schema": SCHEMA, "column": column},
        ).first()
    )


def _has_constraint(bind, constraint: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE constraint_schema = :schema AND constraint_name = :name"
            ),
            {"schema": SCHEMA, "name": constraint},
        ).first()
    )


def upgrade() -> None:
    bind = op.get_bind()

    if not _has_column(bind, "declaration_active"):
        op.add_column(
            "schedules",
            sa.Column("declaration_active", sa.Boolean, server_default=sa.text("true"), nullable=False),
            schema=SCHEMA,
        )
    if not _has_column(bind, "last_declared_at"):
        op.add_column(
            "schedules",
            sa.Column("last_declared_at", sa.DateTime(timezone=True), nullable=True),
            schema=SCHEMA,
        )
    if not _has_column(bind, "operationally_paused_at"):
        op.add_column(
            "schedules",
            sa.Column("operationally_paused_at", sa.DateTime(timezone=True), nullable=True),
            schema=SCHEMA,
        )
    if not _has_column(bind, "operationally_paused_by"):
        op.add_column(
            "schedules",
            sa.Column("operationally_paused_by", sa.String(255), nullable=True),
            schema=SCHEMA,
        )
    if _has_column(bind, "paused"):
        op.drop_column("schedules", "paused", schema=SCHEMA)
    if not _has_constraint(bind, "uq_schedules_task"):
        op.create_unique_constraint(
            "uq_schedules_task", "schedules", ["task_namespace", "task_name"], schema=SCHEMA
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_constraint(bind, "uq_schedules_task"):
        op.drop_constraint("uq_schedules_task", "schedules", schema=SCHEMA)
    if not _has_column(bind, "paused"):
        op.add_column(
            "schedules",
            sa.Column("paused", sa.Boolean, server_default=sa.text("false"), nullable=False),
            schema=SCHEMA,
        )
    for col in ("operationally_paused_by", "operationally_paused_at", "last_declared_at", "declaration_active"):
        if _has_column(bind, col):
            op.drop_column("schedules", col, schema=SCHEMA)
