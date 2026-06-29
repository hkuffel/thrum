"""runs.caller: the Caller frozen at Enqueue

Adds a nullable `caller` JSONB column to `runs` (ADR-0024). The queue cannot supply
a live Caller, so identity is frozen onto the Run at Enqueue and thawed into the
Execution Scope at execution. v1 stamps the system default; NULL means no frozen
Caller (a materialized cron occurrence), thawed to the system default.

Fresh databases already get the column from 0001's model-derived `create_all`, so
this migration is a no-op for them.

Revision ID: 0009_run_caller
Revises: 0008_effects
Create Date: 2026-06-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

from thrum.jobs.models.base import SCHEMA

revision = "0009_run_caller"
down_revision = "0008_effects"
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
    if not _has_column(bind, "caller"):
        op.add_column(
            "runs",
            sa.Column("caller", JSONB(), nullable=True),
            schema=SCHEMA,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "caller"):
        op.drop_column("runs", "caller", schema=SCHEMA)
