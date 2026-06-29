"""Alembic environment for Thrum's self-contained migrations (ADR-0004).

Isolation: the version table lives in the `thrum` schema (never a user's
`public.alembic_version`), `include_schemas` is on, and an `include_object`
filter ignores anything outside `thrum` so autogenerate can never reach into
the user's own tables.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from thrum.jobs.db.migrations import VERSION_TABLE, VERSION_TABLE_SCHEMA
from thrum.jobs.models import Base

config = context.config
target_metadata = Base.metadata


def _include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001, ANN202
    # Never touch anything outside the thrum schema.
    if type_ in {"table", "column"}:
        table = getattr(obj, "table", None)
        schema = getattr(obj, "schema", None) or getattr(table, "schema", None)
        return schema == VERSION_TABLE_SCHEMA
    return True


def _configure(connection=None) -> None:  # noqa: ANN001
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
    _configure()
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # The schema must exist before the version table can be created in it.
        connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{VERSION_TABLE_SCHEMA}"'))
        connection.commit()
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
