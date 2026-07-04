"""The read-only Control Plane against real Postgres (ADR-0027, ADR-0028).

Covers the synchronous keystone: the Execution Scope core run in isolation (no
Worker, no transport), the write-blocking read-only `db` Provider, and the
seeded-DB `thrum runs list` end to end — proving the built-in lights up inline
against the developer's Postgres and creates no Run, Attempt, or Effect.

Skips gracefully without Docker via the `session_factory` / `migrated_dsn`
fixtures.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from factories import make_operation
from thrum.jobs.builtins import build_control_plane
from thrum.jobs.cli import main
from thrum.jobs.enqueue import enqueue
from thrum.jobs.models import Attempt, Effect, Run
from thrum.jobs.providers import Caller, ProviderContext, ReadOnly, db_provider
from thrum.jobs.scope import ExecutionScope


async def _counts(session_factory) -> tuple[int, int, int]:
    async with session_factory() as session:
        runs = (await session.execute(select(func.count()).select_from(Run))).scalar_one()
        attempts = (await session.execute(select(func.count()).select_from(Attempt))).scalar_one()
        effects = (await session.execute(select(func.count()).select_from(Effect))).scalar_one()
    return runs, attempts, effects


# The Execution Scope core, in isolation


async def test_scope_invoke_returns_output_and_creates_no_run(session_factory):
    async def reader(*, db: AsyncSession) -> dict:
        value = (await db.execute(text("SELECT 42"))).scalar_one()
        return {"answer": value}

    operation = make_operation(fn=reader, namespace="probe", name="reader")
    scope = ExecutionScope(session_factory, {AsyncSession: db_provider})

    output = await scope.invoke(operation, {}, Caller.system())

    assert output == {"answer": 42}
    assert await _counts(session_factory) == (0, 0, 0)


# The read-only `db` Provider: a read works, a write raises


async def test_read_only_provider_allows_reads_blocks_writes(session_factory):
    async with session_factory() as session, session.begin():
        ctx = ProviderContext(session=session, attenuations=(ReadOnly,))
        async with db_provider(ctx, Caller.system()) as db:
            assert (await db.execute(text("SELECT 1"))).scalar_one() == 1
            with pytest.raises(PermissionError):
                await db.execute(text("INSERT INTO thrum.runs (id) VALUES (gen_random_uuid())"))


async def test_write_capable_provider_is_the_unmarked_default(session_factory):
    # The same Provider, unmarked, imposes no block — proof read-only is opt-in.
    async with session_factory() as session, session.begin():
        ctx = ProviderContext(session=session, attenuations=())
        async with db_provider(ctx, Caller.system()) as db:
            await db.execute(text("CREATE TEMP TABLE probe_writeable (id int)"))
            await db.execute(text("INSERT INTO probe_writeable (id) VALUES (1)"))
            assert (
                await db.execute(text("SELECT count(*) FROM probe_writeable"))
            ).scalar_one() == 1


# End to end: seeded DB, `thrum runs list` lights up unfiltered


async def test_runs_list_projects_seeded_runs_and_records_nothing(session_factory, migrated_dsn):
    async with session_factory() as session:
        first = enqueue(session, "billing.charge", customer_id=1)
        second = enqueue(session, "billing.refund", customer_id=2)
        await session.commit()
        seeded = {str(first.id), str(second.id)}

    from thrum.jobs.projection.cli import project

    app = build_control_plane()
    operation = app.operations["thrum.list_runs"]
    rendered = await project(app, operation, migrated_dsn, argv=[], as_json=True)

    listed = json.loads(rendered)
    assert {row["id"] for row in listed} == seeded
    assert all(row["operation"].startswith("billing.") for row in listed)
    # Reached synchronously: no Run, Attempt, or Effect beyond the two seeded Runs.
    assert await _counts(session_factory) == (2, 0, 0)


def test_runs_list_cli_command_runs_with_no_server(migrated_dsn):
    # The actual `thrum runs list` wiring, driven through Click against a bare
    # Postgres — no Worker, no server, exercising the asyncio.run entry point.
    result = CliRunner().invoke(main, ["runs", "list", "--dsn", migrated_dsn, "--json"])

    assert result.exit_code == 0, result.output
    assert isinstance(json.loads(result.output), list)
