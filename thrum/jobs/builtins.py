"""The Control Plane's built-in observe-and-command Operations.

These read-only introspection Operations run through the same Execution Scope as
user Operations and are projected onto transports (CLI, HTTP, MCP) as co-equal
projections, not clients of an API.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from thrum.jobs.app import App
from thrum.jobs.models import Run
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
async def list_runs(*, db: ReadOnly[AsyncSession]) -> list[RunView]:
    """List all Runs newest-first. Read-only, so exempt from the Zero-Effect flag."""
    runs = (await db.execute(select(Run).order_by(Run.created_at.desc()))).scalars().all()
    return [RunView.from_run(run) for run in runs]


def build_control_plane() -> App:
    """Assemble and compile the App hosting the Control Plane Operations."""
    app = App(registry=control_plane)
    app.provide(AsyncSession, db_provider)
    app.compile()
    return app
