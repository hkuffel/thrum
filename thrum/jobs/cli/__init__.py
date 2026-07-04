from __future__ import annotations

import click


@click.group()
@click.version_option()
def main() -> None:
    pass


@main.command()
@click.option("--dsn", envvar="THRUM_DSN", required=True)
def worker(dsn: str) -> None:
    import asyncio

    from thrum.jobs.config import WorkerConfig
    from thrum.jobs.worker import Worker

    asyncio.run(Worker(WorkerConfig(dsn=dsn)).run())


@main.group()
def runs() -> None:
    pass


@runs.command("list")
@click.option("--dsn", envvar="THRUM_DSN", required=True)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of a table.")
def runs_list(dsn: str, as_json: bool) -> None:
    import asyncio

    from thrum.jobs.builtins import build_control_plane
    from thrum.jobs.projection.cli import project

    app = build_control_plane()
    operation = app.operations["thrum.list_runs"]
    click.echo(asyncio.run(project(app, operation, dsn, argv=[], as_json=as_json)))


@main.group()
def db() -> None:
    pass


@db.command("upgrade")
@click.option("--dsn", envvar="THRUM_DSN", required=True)
def db_upgrade(dsn: str) -> None:
    from thrum.jobs.db.migrations import upgrade

    upgrade(dsn)


@main.command()
@click.option("--dsn", envvar="THRUM_DSN", required=True)
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=8000, type=int)
def server(dsn: str, host: str, port: int) -> None:
    import uvicorn

    from thrum.jobs.server import create_app

    uvicorn.run(create_app(), host=host, port=port)
