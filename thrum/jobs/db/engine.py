"""Engine helpers. The async engine is the Worker's primary path (ADR-0005); the
sync engine backs migrations and the CLI."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy import Engine
    from sqlalchemy.ext.asyncio import AsyncEngine


def make_async_engine(dsn: str) -> AsyncEngine:
    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine(_as_async_dsn(dsn))


def make_sync_engine(dsn: str) -> Engine:
    from sqlalchemy import create_engine

    return create_engine(_as_sync_dsn(dsn))


def _swap_driver(dsn: str, driver: str) -> str:
    """Force a specific DBAPI driver onto a Postgres DSN, regardless of which
    driver (if any) the source URL named — e.g. testcontainers hands back a
    `postgresql+psycopg2://` URL we must not feed to our psycopg-v3 / asyncpg
    engines."""
    scheme, sep, rest = dsn.partition("://")
    base = scheme.split("+", 1)[0]  # drop any existing +driver
    return f"{base}+{driver}{sep}{rest}"


def _as_async_dsn(dsn: str) -> str:
    return _swap_driver(dsn, "asyncpg")


def _as_sync_dsn(dsn: str) -> str:
    return _swap_driver(dsn, "psycopg")
