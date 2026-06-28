"""schedules unique key: (task_namespace, task_name) -> (.., cron)

A single operation may now declare several recurrences via repeated
`op.schedule(...)` calls. The old `uq_schedules_task` constraint allowed only
one row per (namespace, name), so the reconcile upsert silently collapsed
sibling schedules onto the last-declared one. Widening the unique key to
include `cron` makes each recurrence its own row while the ON CONFLICT upsert
still updates a re-declared recurrence in place.

This predates the task_* -> operation_* column rename (0007), so it speaks the
task_* names of its own era. Fresh databases already get the model-derived
`uq_schedules_operation_cron` from 0001's `create_all`, so the constraint
create here is guarded on the legacy task_* columns being present.

Revision ID: 0006_schedule_cron_identity
Revises: 0005_run_max_attempts
Create Date: 2026-06-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from thrum.jobs.models.base import SCHEMA

revision = "0006_schedule_cron_identity"
down_revision = "0005_run_max_attempts"
branch_labels = None
depends_on = None

OLD_NAME = "uq_schedules_task"
NEW_NAME = "uq_schedules_task_cron"


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


def upgrade() -> None:
    bind = op.get_bind()
    if _has_constraint(bind, OLD_NAME):
        op.drop_constraint(OLD_NAME, "schedules", schema=SCHEMA, type_="unique")
    if _has_column(bind, "task_namespace") and not _has_constraint(bind, NEW_NAME):
        op.create_unique_constraint(
            NEW_NAME,
            "schedules",
            ["task_namespace", "task_name", "cron"],
            schema=SCHEMA,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_constraint(bind, NEW_NAME):
        op.drop_constraint(NEW_NAME, "schedules", schema=SCHEMA, type_="unique")
    # The widened constraint may have allowed multiple cron rows per operation.
    # The old (task_namespace, task_name) constraint cannot tolerate them, so
    # collapse each operation to its earliest-inserted row before recreating it.
    bind.execute(
        sa.text(
            f'DELETE FROM "{SCHEMA}".schedules '
            "WHERE ctid NOT IN ("
            "SELECT MIN(ctid) FROM "
            f'"{SCHEMA}".schedules '
            "GROUP BY task_namespace, task_name"
            ")"
        )
    )
    if not _has_constraint(bind, OLD_NAME):
        op.create_unique_constraint(
            OLD_NAME,
            "schedules",
            ["task_namespace", "task_name"],
            schema=SCHEMA,
        )
