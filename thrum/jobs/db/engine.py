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
    scheme, sep, rest = dsn.partition("://")
    base = scheme.split("+", 1)[0]
    return f"{base}+{driver}{sep}{rest}"


def _as_async_dsn(dsn: str) -> str:
    return _swap_driver(dsn, "asyncpg")


def _as_sync_dsn(dsn: str) -> str:
    return _swap_driver(dsn, "psycopg")
