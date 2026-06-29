"""Thrum-owned migrations (ADR-0004). Alembic is used INTERNALLY but fully
self-contained: a disjoint environment with its own version table in the
`thrum` schema, never entangled with the user's Alembic/Django/none. Driven by
`thrum db upgrade`."""

from __future__ import annotations

from pathlib import Path

# Alembic's own version table, namespaced into our schema so it never collides
# with a user's `alembic_version` in `public`.
VERSION_TABLE = "thrum_version"
VERSION_TABLE_SCHEMA = "thrum"

_MIGRATIONS_DIR = Path(__file__).parent


def _alembic_config(dsn: str):
    """Build an Alembic Config entirely in memory (no alembic.ini) pointed at
    this package's self-contained migration environment."""
    from alembic.config import Config

    from thrum.jobs.db.engine import _as_sync_dsn

    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", _as_sync_dsn(dsn))
    return cfg


def upgrade(dsn: str, revision: str = "head") -> None:
    """Apply migrations up to `revision` against the `thrum` schema. Idempotent:
    Alembic's version table (`thrum.thrum_version`) makes a repeat run a no-op.
    Driven by `thrum db upgrade` (ADR-0004)."""
    from alembic import command

    command.upgrade(_alembic_config(dsn), revision)
