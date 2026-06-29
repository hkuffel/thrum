"""The `thrum` CLI. Subcommands import their heavy machinery lazily inside the
command body, so `thrum db upgrade` never loads the Worker and nothing here
drags in the `[server]` stack unless `thrum server` is actually invoked. This is
the CLI half of the import-discipline law (pyproject.toml)."""

from __future__ import annotations

import click


@click.group()
@click.version_option()
def main() -> None:
    """Thrum — Postgres-native background jobs."""


@main.command()
@click.option("--dsn", envvar="THRUM_DSN", required=True)
def worker(dsn: str) -> None:
    """Run a Worker (and contend for the scheduler role)."""
    import asyncio

    from thrum.jobs.config import WorkerConfig
    from thrum.jobs.worker import Worker

    asyncio.run(Worker(WorkerConfig(dsn=dsn)).run())


@main.group()
def db() -> None:
    """Manage Thrum's dedicated `thrum` schema (ADR-0004)."""


@db.command("upgrade")
@click.option("--dsn", envvar="THRUM_DSN", required=True)
def db_upgrade(dsn: str) -> None:
    """Apply Thrum's migrations to the `thrum` schema."""
    from thrum.jobs.db.migrations import upgrade

    upgrade(dsn)


@main.command()
@click.option("--dsn", envvar="THRUM_DSN", required=True)
@click.option("--host", default="127.0.0.1")
@click.option("--port", default=8000, type=int)
def server(dsn: str, host: str, port: int) -> None:
    """Run the control plane + dashboard (requires `thrum[server]`)."""
    import uvicorn

    from thrum.jobs.server import create_app

    uvicorn.run(create_app(), host=host, port=port)
