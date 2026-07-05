from __future__ import annotations

import pytest
import pytest_asyncio


@pytest.fixture(autouse=True)
def _clean_global_registry():
    """Isolate each test's Operation declarations from the process-global registry.

    Operations register into process-wide tables at import, so without this a
    decorator in one test would collide with or leak into the next. Snapshots
    and restores both tables around every test.
    """
    from thrum import Registry

    saved_ops = Registry._global.copy()
    saved_schedules = Registry._global_schedules.copy()
    Registry._global.clear()
    Registry._global_schedules.clear()
    try:
        yield
    finally:
        Registry._global.clear()
        Registry._global.update(saved_ops)
        Registry._global_schedules.clear()
        Registry._global_schedules.update(saved_schedules)


@pytest.fixture(scope="session")
def postgres_dsn() -> str:
    """A throwaway Postgres via testcontainers, skipping if it is unavailable."""
    try:
        from testcontainers.postgres import PostgresContainer
    except ModuleNotFoundError:
        pytest.skip("testcontainers not installed")

    try:
        with PostgresContainer("postgres:16") as pg:
            yield pg.get_connection_url()
    except Exception as exc:
        pytest.skip(f"Postgres container unavailable: {exc}")


@pytest.fixture(scope="session")
def migrated_dsn(postgres_dsn: str) -> str:
    """The container DSN with Thrum's schema migrated once for the whole session."""
    from thrum.jobs.db.migrations import upgrade

    upgrade(postgres_dsn)
    return postgres_dsn


@pytest_asyncio.fixture
async def session_factory(migrated_dsn: str):
    """A session factory against a freshly truncated schema, isolating each test."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from thrum.jobs.db.engine import make_async_engine

    engine = make_async_engine(migrated_dsn)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE thrum.effects, thrum.attempts, thrum.runs, thrum.schedules "
                "RESTART IDENTITY CASCADE"
            )
        )
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
