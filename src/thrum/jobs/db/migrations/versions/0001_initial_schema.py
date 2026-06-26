"""initial schema: schedules, runs, attempts

The initial revision derives the schema directly from the SQLAlchemy models
(`Base.metadata`) rather than hand-written DDL, so the migration and the runtime
models cannot drift at the v1 baseline (PRD decision). Idempotent via
`create_all(checkfirst=True)`.

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from thrum.jobs.models import Base
from thrum.jobs.models.base import SCHEMA

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"'))
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
