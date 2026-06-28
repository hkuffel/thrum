"""effects: per-Attempt, table-level mutation records

Adds the `effects` table (ADR-0024): one row per (table, kind) per Attempt with a
`row_count`. The Effect recorder writes these into Txn 2 so they commit atomically
with the operation's own effects.

The 0001 baseline is model-derived (`Base.metadata.create_all`), so a fresh
database already has the table; this migration only creates it for databases that
predate it, guarded on the table's absence. The table itself is derived from the
model so the migration and runtime cannot drift.

Revision ID: 0008_effects
Revises: 0007_schedule_op_rename
Create Date: 2026-06-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from thrum.jobs.models import Effect
from thrum.jobs.models.base import SCHEMA


revision = "0008_effects"
down_revision = "0007_schedule_op_rename"
branch_labels = None
depends_on = None


def _has_table(bind, table: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = :schema AND table_name = :table"
            ),
            {"schema": SCHEMA, "table": table},
        ).first()
    )


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "effects"):
        Effect.__table__.create(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, "effects"):
        Effect.__table__.drop(bind=bind)
