"""The Control Plane's built-in observe-and-command Operations.

These read-only introspection Operations run through the same Execution Scope as
user Operations and are projected onto transports (CLI, HTTP, MCP) as co-equal
projections, not clients of an API.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from thrum.jobs.app import App
from thrum.jobs.models import Run, RunStatus
from thrum.jobs.providers import ReadOnly, db_provider
from thrum.jobs.registry import Registry

control_plane = Registry("thrum")


@dataclass(frozen=True)
class RunView:
    """A Run flattened to serializable strings for projection to a transport."""

    id: str
    operation: str
    status: str
    trigger: str
    created_at: str

    @classmethod
    def from_run(cls, run: Run) -> RunView:
        """Build a view from a Run row."""
        return cls(
            id=str(run.id),
            operation=f"{run.operation_namespace}.{run.operation_name}",
            status=str(run.status),
            trigger=str(run.trigger),
            created_at=run.created_at.isoformat(),
        )


@control_plane.operation(name="list_runs")
async def list_runs(
    *,
    db: ReadOnly[AsyncSession],
    status: str | None = None,
    operation: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[RunView]:
    """List Runs newest-first, narrowed by any supplied filters.

    Filters compose: ``status`` scopes to one lifecycle state (``missed`` surfaces
    the cron wedge), ``operation`` to one ``namespace.name`` identity, and
    ``since`` / ``until`` to a ``created_at`` window with each bound independent.
    Read-only, so exempt from the Zero-Effect flag.

    Raises:
        ValueError: If a filter value is malformed — an unknown status, an
            operation key without a namespace, or a non-ISO timestamp.
    """
    query = select(Run).order_by(Run.created_at.desc())
    query = _apply_filters(query, status, operation, since, until)
    runs = (await db.execute(query)).scalars().all()
    return [RunView.from_run(run) for run in runs]


def _apply_filters(
    query: Select[tuple[Run]],
    status: str | None,
    operation: str | None,
    since: str | None,
    until: str | None,
) -> Select[tuple[Run]]:
    """Narrow the Runs query by each supplied filter, validating as it goes."""
    if status is not None:
        query = query.where(Run.status == _parse_status(status))
    if operation is not None:
        namespace, name = _parse_operation(operation)
        query = query.where(Run.operation_namespace == namespace, Run.operation_name == name)
    if since is not None:
        query = query.where(Run.created_at >= _parse_timestamp("since", since))
    if until is not None:
        query = query.where(Run.created_at <= _parse_timestamp("until", until))
    return query


def _parse_status(value: str) -> RunStatus:
    """Resolve a status filter to a RunStatus, naming the valid set on a miss."""
    try:
        return RunStatus(value)
    except ValueError:
        allowed = ", ".join(member.value for member in RunStatus)
        raise ValueError(f"unknown status {value!r}; expected one of: {allowed}") from None


def _parse_operation(value: str) -> tuple[str, str]:
    """Split an ``namespace.name`` operation filter into its two parts."""
    namespace, sep, name = value.rpartition(".")
    if not sep or not namespace or not name:
        raise ValueError(f"invalid operation {value!r}; expected 'namespace.name'")
    return namespace, name


def _parse_timestamp(bound: str, value: str) -> dt.datetime:
    """Parse an ISO 8601 window bound, naming which bound failed."""
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"invalid --{bound} timestamp {value!r}; expected ISO 8601") from None


def build_control_plane() -> App:
    """Assemble and compile the App hosting the Control Plane Operations."""
    app = App(registry=control_plane)
    app.provide(AsyncSession, db_provider)
    app.compile()
    return app
