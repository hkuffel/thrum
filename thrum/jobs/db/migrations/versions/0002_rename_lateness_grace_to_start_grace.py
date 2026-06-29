"""rename schedules.lateness_grace -> start_grace

The 0001 baseline is model-derived (`Base.metadata.create_all`), so a *fresh*
database already gets the renamed `start_grace` column directly from the models.
This migration therefore only fixes databases that were created *before* the
rename: it is guarded to rename solely when the legacy `lateness_grace` column is
still present, making it a no-op on fresh DBs.

Term rename only (Start Grace, CONTEXT.md) — no semantic change.

Revision ID: 0002_start_grace
Revises: 0001_initial
Create Date: 2026-06-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from thrum.jobs.models.base import SCHEMA

revision = "0002_start_grace"
down_revision = "0001_initial"
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


def upgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "lateness_grace") and not _has_column(bind, "start_grace"):
        op.alter_column(
            "schedules",
            "lateness_grace",
            new_column_name="start_grace",
            schema=SCHEMA,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "start_grace") and not _has_column(bind, "lateness_grace"):
        op.alter_column(
            "schedules",
            "start_grace",
            new_column_name="lateness_grace",
            schema=SCHEMA,
        )
