"""Alembic environment for Thrum's schema-scoped migrations.

Autogeneration and comparison are confined to Thrum's own schema so a user's
tables in the same database are never seen as drift to be dropped.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from thrum.jobs.db.migrations import VERSION_TABLE, VERSION_TABLE_SCHEMA
from thrum.jobs.models import Base

config = context.config
target_metadata = Base.metadata


def _include_object(obj, name, type_, reflected, compare_to):
    """Restrict Alembic to tables and columns in Thrum's own schema."""
    if type_ in {"table", "column"}:
        table = getattr(obj, "table", None)
        schema = getattr(obj, "schema", None) or getattr(table, "schema", None)
        return schema == VERSION_TABLE_SCHEMA
    return True


def _configure(connection=None) -> None:
    """Configure the migration context for online or offline running."""
    context.configure(
        connection=connection,
        url=None if connection else config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        version_table=VERSION_TABLE,
        version_table_schema=VERSION_TABLE_SCHEMA,
        include_schemas=True,
        include_object=_include_object,
    )


def run_migrations_offline() -> None:
    """Emit migration SQL against a URL, without a live connection."""
    _configure()
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection, creating the schema first."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{VERSION_TABLE_SCHEMA}"'))
        connection.commit()
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
