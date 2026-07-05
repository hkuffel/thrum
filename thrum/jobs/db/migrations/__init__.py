"""Programmatic entry point for applying Thrum's schema migrations.

Thrum owns its migrations and version table, both scoped to its dedicated
schema, so it never touches Alembic state the user's own app may keep.
"""

from __future__ import annotations

from pathlib import Path

VERSION_TABLE = "thrum_version"
VERSION_TABLE_SCHEMA = "thrum"

_MIGRATIONS_DIR = Path(__file__).parent


def _alembic_config(dsn: str):
    """Build an Alembic config pointed at Thrum's migrations and a sync DSN."""
    from alembic.config import Config

    from thrum.jobs.db.engine import _as_sync_dsn

    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", _as_sync_dsn(dsn))
    return cfg


def upgrade(dsn: str, revision: str = "head") -> None:
    """Apply migrations up to ``revision`` (``head`` by default)."""
    from alembic import command

    command.upgrade(_alembic_config(dsn), revision)
