"""rename schedules.task_namespace/task_name -> operation_namespace/operation_name

ADR-0023 retires "Task" from the vocabulary; the `schedules` table now speaks
`operation_*` like the `runs` table (0004) and the code. The 0001 baseline is
model-derived, so a fresh database already gets the renamed columns and the
`uq_schedules_operation_cron` constraint directly from the models. This migration
only fixes databases created before the rename: it is guarded to act solely when
the legacy `task_*` columns / constraint are still present.

Pre-release rename, no semantic change — no production data yet.

Revision ID: 0007_schedule_op_rename
Revises: 0006_schedule_cron_identity
Create Date: 2026-06-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from thrum.jobs.models.base import SCHEMA

revision = "0007_schedule_op_rename"
down_revision = "0006_schedule_cron_identity"
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


def _has_constraint(bind, name: str) -> bool:
    return bool(
        bind.execute(
            sa.text(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_schema = :schema AND table_name = 'schedules' "
                "AND constraint_name = :name"
            ),
            {"schema": SCHEMA, "name": name},
        ).first()
    )


def _rename_constraint(old: str, new: str) -> None:
    op.execute(f'ALTER TABLE "{SCHEMA}".schedules RENAME CONSTRAINT "{old}" TO "{new}"')


def upgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "task_namespace") and not _has_column(bind, "operation_namespace"):
        op.alter_column(
            "schedules", "task_namespace", new_column_name="operation_namespace", schema=SCHEMA
        )
    if _has_column(bind, "task_name") and not _has_column(bind, "operation_name"):
        op.alter_column(
            "schedules", "task_name", new_column_name="operation_name", schema=SCHEMA
        )
    if _has_constraint(bind, "uq_schedules_task_cron") and not _has_constraint(
        bind, "uq_schedules_operation_cron"
    ):
        _rename_constraint("uq_schedules_task_cron", "uq_schedules_operation_cron")


def downgrade() -> None:
    bind = op.get_bind()
    if _has_constraint(bind, "uq_schedules_operation_cron") and not _has_constraint(
        bind, "uq_schedules_task_cron"
    ):
        _rename_constraint("uq_schedules_operation_cron", "uq_schedules_task_cron")
    if _has_column(bind, "operation_namespace") and not _has_column(bind, "task_namespace"):
        op.alter_column(
            "schedules", "operation_namespace", new_column_name="task_namespace", schema=SCHEMA
        )
    if _has_column(bind, "operation_name") and not _has_column(bind, "task_name"):
        op.alter_column(
            "schedules", "operation_name", new_column_name="task_name", schema=SCHEMA
        )
