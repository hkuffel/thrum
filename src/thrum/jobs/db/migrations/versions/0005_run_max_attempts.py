"""runs.max_attempts: per-call durability override (issue #6)

Adds a nullable `max_attempts` integer column to `runs`. NULL means the Worker
inherits the Operation's default retry policy; a non-null value carries the
per-call override set by `op.enqueue(..., retries=N)` as N+1 (attempt budget).

Only the queue projection (enqueue) writes this column; non-durable projections
(future HTTP) must leave it NULL so durability semantics don't bleed across
projection types.

Fresh databases already get the column from 0001's model-derived `create_all`,
so this migration is a no-op for them.

Revision ID: 0005_run_max_attempts
Revises: 0004_op_rename
Create Date: 2026-06-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from thrum.jobs.models.base import SCHEMA

revision = "0005_run_max_attempts"
down_revision = "0004_op_rename"
branch_labels = None
depends_on = None


def _has_column(bind, column: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = 'runs' "
                "AND column_name = :column"
            ),
            {"schema": SCHEMA, "column": column},
        ).first()
    )


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "max_attempts"):
        op.add_column(
            "runs",
            sa.Column("max_attempts", sa.Integer(), nullable=True),
            schema=SCHEMA,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "max_attempts"):
        op.drop_column("runs", "max_attempts", schema=SCHEMA)
