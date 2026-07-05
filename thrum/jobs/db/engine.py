"""Engine factories that pin the driver so a plain DSN works either way.

A user's DSN names a database, not a driver; these force the right driver
(``asyncpg`` for the async engine, ``psycopg`` for the sync engine) so the same
DSN serves the Worker's async path and the migrations' sync path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy import Engine
    from sqlalchemy.ext.asyncio import AsyncEngine


def make_async_engine(dsn: str) -> AsyncEngine:
    """Create an async engine, forcing the asyncpg driver."""
    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine(_as_async_dsn(dsn))


def make_sync_engine(dsn: str) -> Engine:
    """Create a sync engine, forcing the psycopg driver."""
    from sqlalchemy import create_engine

    return create_engine(_as_sync_dsn(dsn))


def _swap_driver(dsn: str, driver: str) -> str:
    """Rewrite a DSN's scheme to ``dialect+driver``, replacing any existing driver."""
    scheme, sep, rest = dsn.partition("://")
    base = scheme.split("+", 1)[0]
    return f"{base}+{driver}{sep}{rest}"


def _as_async_dsn(dsn: str) -> str:
    return _swap_driver(dsn, "asyncpg")


def _as_sync_dsn(dsn: str) -> str:
    return _swap_driver(dsn, "psycopg")
