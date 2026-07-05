"""The ``thrum`` command-line entry point.

Each command imports its dependencies lazily so ``thrum --help`` stays fast and
optional extras (the server) are needed only by the command that uses them.
Command docstrings are the CLI help text.
"""

from __future__ import annotations

import click


@click.group()
@click.version_option()
def main() -> None:
    """Thrum — a Postgres-native background-jobs system."""


@main.command()
@click.option("--dsn", envvar="THRUM_DSN", required=True)
def worker(dsn: str) -> None:
    """Run a Worker that claims and executes due work."""
    import asyncio

    from thrum.jobs.config import WorkerConfig
    from thrum.jobs.worker import Worker

    asyncio.run(Worker(WorkerConfig(dsn=dsn)).run())


@main.group()
def runs() -> None:
    """Inspect Runs."""


@runs.command("list")
@click.option("--dsn", envvar="THRUM_DSN", required=True)
@click.option("--status", help="Filter by lifecycle status (e.g. succeeded, failed, missed).")
@click.option("--operation", "operation_key", help="Filter by Operation identity (namespace.name).")
@click.option("--since", help="Only Runs created at or after this ISO 8601 timestamp.")
@click.option("--until", help="Only Runs created at or before this ISO 8601 timestamp.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of a table.")
def runs_list(
    dsn: str,
    status: str | None,
    operation_key: str | None,
    since: str | None,
    until: str | None,
    as_json: bool,
) -> None:
    """List Runs by invoking the control-plane Operation inline against Postgres."""
    import asyncio

    from thrum.jobs.builtins import build_control_plane
    from thrum.jobs.projection.cli import project

    argv: list[str] = []
    for name, value in (
        ("status", status),
        ("operation", operation_key),
        ("since", since),
        ("until", until),
    ):
        if value is not None:
            argv += [f"--{name}", value]

    app = build_control_plane()
    operation = app.operations["thrum.list_runs"]
    try:
        rendered = asyncio.run(project(app, operation, dsn, argv=argv, as_json=as_json))
    except ValueError as exc:
        # Turn a malformed filter value into a clean CLI error, not a traceback.
        raise click.ClickException(str(exc)) from exc
    click.echo(rendered)


@main.group()
def db() -> None:
    """Manage Thrum's database schema."""


@db.command("upgrade")
@click.option("--dsn", envvar="THRUM_DSN", required=True)
def db_upgrade(dsn: str) -> None:
    """Apply outstanding migrations to bring the schema up to date."""
    from thrum.jobs.db.migrations import upgrade

    upgrade(dsn)


@main.command()
@click.option("--dsn", envvar="THRUM_DSN", required=True)
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=8000, type=int)
def server(dsn: str, host: str, port: int) -> None:
    """Serve the control plane's HTTP projection."""
    import uvicorn

    from thrum.jobs.server import create_app

    uvicorn.run(create_app(), host=host, port=port)
