"""rename runs.task_namespace/task_name -> operation_namespace/operation_name

ADR-0023 retires "Task" from authoring vocabulary. The Run columns now speak the
same language as the code (`@operation`) and docs. The 0001 baseline is
model-derived (`Base.metadata.create_all`), so a fresh database already gets the
renamed columns directly from the models. This migration only fixes databases
created before the rename: it is guarded to rename solely when the legacy `task_*`
columns are still present, making it a no-op on fresh DBs.

Pre-release rename, no semantic change — no production data yet.

Revision ID: 0004_op_rename
Revises: 0003_two_gate
Create Date: 2026-06-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from thrum.jobs.models.base import SCHEMA

revision = "0004_op_rename"
down_revision = "0003_two_gate"
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
    if _has_column(bind, "task_namespace") and not _has_column(bind, "operation_namespace"):
        op.alter_column(
            "runs",
            "task_namespace",
            new_column_name="operation_namespace",
            schema=SCHEMA,
        )
    if _has_column(bind, "task_name") and not _has_column(bind, "operation_name"):
        op.alter_column(
            "runs",
            "task_name",
            new_column_name="operation_name",
            schema=SCHEMA,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "operation_namespace") and not _has_column(bind, "task_namespace"):
        op.alter_column(
            "runs",
            "operation_namespace",
            new_column_name="task_namespace",
            schema=SCHEMA,
        )
    if _has_column(bind, "operation_name") and not _has_column(bind, "task_name"):
        op.alter_column(
            "runs",
            "operation_name",
            new_column_name="task_name",
            schema=SCHEMA,
        )
