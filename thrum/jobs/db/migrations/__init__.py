from __future__ import annotations

from pathlib import Path

VERSION_TABLE = "thrum_version"
VERSION_TABLE_SCHEMA = "thrum"

_MIGRATIONS_DIR = Path(__file__).parent


def _alembic_config(dsn: str):
    from alembic.config import Config

    from thrum.jobs.db.engine import _as_sync_dsn

    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", _as_sync_dsn(dsn))
    return cfg


def upgrade(dsn: str, revision: str = "head") -> None:
    from alembic import command

    command.upgrade(_alembic_config(dsn), revision)
