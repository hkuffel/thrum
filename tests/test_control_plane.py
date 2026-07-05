from __future__ import annotations

import datetime as dt
import json

import pytest
from click.testing import CliRunner
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from factories import make_operation
from thrum.jobs.builtins import build_control_plane
from thrum.jobs.cli import main
from thrum.jobs.enqueue import enqueue
from thrum.jobs.models import Attempt, Effect, Run, RunStatus, Trigger
from thrum.jobs.providers import Caller, ProviderContext, ReadOnly, db_provider
from thrum.jobs.scope import ExecutionScope


async def _counts(session_factory) -> tuple[int, int, int]:
    async with session_factory() as session:
        runs = (await session.execute(select(func.count()).select_from(Run))).scalar_one()
        attempts = (await session.execute(select(func.count()).select_from(Attempt))).scalar_one()
        effects = (await session.execute(select(func.count()).select_from(Effect))).scalar_one()
    return runs, attempts, effects


async def test_scope_invoke_returns_output_and_creates_no_run(session_factory):
    async def reader(*, db: AsyncSession) -> dict:
        value = (await db.execute(text("SELECT 42"))).scalar_one()
        return {"answer": value}

    operation = make_operation(fn=reader, namespace="probe", name="reader")
    scope = ExecutionScope(session_factory, {AsyncSession: db_provider})

    output = await scope.invoke(operation, {}, Caller.system())

    assert output == {"answer": 42}
    assert await _counts(session_factory) == (0, 0, 0)


async def test_read_only_provider_allows_reads_blocks_writes(session_factory):
    async with session_factory() as session, session.begin():
        ctx = ProviderContext(session=session, attenuations=(ReadOnly,))
        async with db_provider(ctx, Caller.system()) as db:
            assert (await db.execute(text("SELECT 1"))).scalar_one() == 1
            with pytest.raises(PermissionError):
                await db.execute(text("INSERT INTO thrum.runs (id) VALUES (gen_random_uuid())"))


async def test_write_capable_provider_is_the_unmarked_default(session_factory):
    async with session_factory() as session, session.begin():
        ctx = ProviderContext(session=session, attenuations=())
        async with db_provider(ctx, Caller.system()) as db:
            await db.execute(text("CREATE TEMP TABLE probe_writeable (id int)"))
            await db.execute(text("INSERT INTO probe_writeable (id) VALUES (1)"))
            assert (
                await db.execute(text("SELECT count(*) FROM probe_writeable"))
            ).scalar_one() == 1


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
    assert await _counts(session_factory) == (2, 0, 0)


def test_runs_list_cli_command_runs_with_no_server(migrated_dsn):
    result = CliRunner().invoke(main, ["runs", "list", "--dsn", migrated_dsn, "--json"])

    assert result.exit_code == 0, result.output
    assert isinstance(json.loads(result.output), list)


async def _seed_run(
    session_factory,
    *,
    operation: str = "billing.charge",
    status: RunStatus = RunStatus.pending,
    created_at: dt.datetime | None = None,
) -> str:
    """Add one Run with the given identity, status, and creation time."""
    namespace, _, name = operation.rpartition(".")
    run = Run(
        operation_namespace=namespace,
        operation_name=name,
        trigger=Trigger.enqueue,
        status=status,
        created_at=created_at or dt.datetime.now(dt.UTC),
    )
    async with session_factory() as session, session.begin():
        session.add(run)
    return str(run.id)


async def _list(session_factory, migrated_dsn, argv: list[str]) -> list[dict]:
    """Run the list_runs projection with CLI filter tokens and return the rows."""
    app = build_control_plane()
    operation = app.operations["thrum.list_runs"]
    from thrum.jobs.projection.cli import project

    rendered = await project(app, operation, migrated_dsn, argv=argv, as_json=True)
    return json.loads(rendered)


async def test_filter_by_status_returns_only_that_status(session_factory, migrated_dsn):
    failed = await _seed_run(session_factory, status=RunStatus.failed)
    missed = await _seed_run(session_factory, status=RunStatus.missed)
    await _seed_run(session_factory, status=RunStatus.succeeded)

    only_failed = await _list(session_factory, migrated_dsn, ["--status", "failed"])
    only_missed = await _list(session_factory, migrated_dsn, ["--status", "missed"])

    assert {r["id"] for r in only_failed} == {failed}
    assert {r["id"] for r in only_missed} == {missed}


async def test_filter_by_operation_isolates_one_identity(session_factory, migrated_dsn):
    charge = await _seed_run(session_factory, operation="billing.charge")
    await _seed_run(session_factory, operation="billing.refund")

    listed = await _list(session_factory, migrated_dsn, ["--operation", "billing.charge"])

    assert {r["id"] for r in listed} == {charge}


async def test_filter_by_time_window_bounds_are_independent(session_factory, migrated_dsn):
    old = await _seed_run(session_factory, created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
    mid = await _seed_run(session_factory, created_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC))
    new = await _seed_run(session_factory, created_at=dt.datetime(2026, 12, 1, tzinfo=dt.UTC))

    since = await _list(session_factory, migrated_dsn, ["--since", "2026-03-01T00:00:00+00:00"])
    until = await _list(session_factory, migrated_dsn, ["--until", "2026-09-01T00:00:00+00:00"])

    assert {r["id"] for r in since} == {mid, new}
    assert {r["id"] for r in until} == {old, mid}


async def test_filters_compose(session_factory, migrated_dsn):
    target = await _seed_run(
        session_factory,
        operation="billing.charge",
        status=RunStatus.failed,
        created_at=dt.datetime(2026, 6, 1, tzinfo=dt.UTC),
    )
    await _seed_run(session_factory, operation="billing.charge", status=RunStatus.succeeded)
    await _seed_run(session_factory, operation="billing.refund", status=RunStatus.failed)

    listed = await _list(
        session_factory,
        migrated_dsn,
        [
            "--status",
            "failed",
            "--operation",
            "billing.charge",
            "--since",
            "2026-01-01T00:00:00+00:00",
        ],
    )

    assert {r["id"] for r in listed} == {target}


async def test_unknown_status_raises_clean_value_error(session_factory, migrated_dsn):
    with pytest.raises(ValueError, match="unknown status 'bogus'"):
        await _list(session_factory, migrated_dsn, ["--status", "bogus"])


async def test_naive_since_raises_clean_value_error(session_factory, migrated_dsn):
    with pytest.raises(ValueError, match="expected an ISO 8601 timestamp with a UTC offset"):
        await _list(session_factory, migrated_dsn, ["--since", "2026-01-01"])


def test_bad_filter_value_is_a_clean_cli_error(migrated_dsn):
    result = CliRunner().invoke(main, ["runs", "list", "--dsn", migrated_dsn, "--status", "bogus"])

    assert result.exit_code == 1
    assert "unknown status 'bogus'" in result.output
    assert "Traceback" not in result.output
