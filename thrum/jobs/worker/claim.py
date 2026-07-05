"""Dispatch — claiming due Runs and opening a Lease on each."""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import and_, func, or_, select

from thrum.jobs.models import Attempt, Run, RunStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class ClaimedRun:
    """A Run a Worker has claimed, carrying what it needs to execute it.

    Attributes:
        attempt_id: The Attempt opened by this claim, on which the Lease lives.
        operation_key: The ``namespace.name`` to look up in the registry.
        caller: The frozen Caller to thaw into the Execution Scope, or ``None``.
    """

    run_id: uuid.UUID
    attempt_id: uuid.UUID
    operation_key: str
    inputs: dict
    caller: dict | None


async def claim_runs(
    session: AsyncSession,
    worker_id: str,
    limit: int,
    lease_ttl: dt.timedelta,
) -> list[ClaimedRun]:
    """Claim up to ``limit`` due Runs, opening an Attempt and Lease on each.

    Selects due work with ``FOR UPDATE SKIP LOCKED`` so concurrent Workers never
    contend on the same row and a slow claim never blocks the dispatch query
    behind its locks. Each claimed Run flips to ``running`` and gains a new
    Attempt stamped with the claiming Worker and the Lease window.

    Args:
        worker_id: Identifier stamped onto the Attempt as ``claimed_by``.
        limit: Maximum number of Runs to claim in this pass.
        lease_ttl: How long the Lease is valid before the Reaper may reclaim it.

    Returns:
        The claimed Runs, empty if none are due or ``limit`` is non-positive.
    """
    if limit <= 0:
        return []

    # Derive the Lease window from the DB clock, not the Worker's wall clock, so
    # all Workers and the Reaper agree on when a Lease expires.
    db_now = (await session.execute(select(func.now()))).scalar_one()
    lease_expires_at = db_now + lease_ttl

    # Two ways a Run is due: an enqueued Run past its retry backoff, or a
    # scheduled occurrence whose Fire Time has arrived.
    stmt = (
        select(Run)
        .where(
            or_(
                and_(
                    Run.status == RunStatus.pending,
                    or_(Run.next_attempt_at.is_(None), Run.next_attempt_at <= func.now()),
                ),
                and_(Run.status == RunStatus.scheduled, Run.fire_time <= func.now()),
            )
        )
        .order_by(Run.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    runs = (await session.execute(stmt)).scalars().all()

    claimed: list[ClaimedRun] = []
    for run in runs:
        prior = (
            await session.execute(
                select(func.coalesce(func.max(Attempt.attempt_number), 0)).where(
                    Attempt.run_id == run.id
                )
            )
        ).scalar_one()
        attempt = Attempt(
            run_id=run.id,
            attempt_number=prior + 1,
            claimed_by=worker_id,
            started_at=db_now,
            lease_expires_at=lease_expires_at,
        )
        session.add(attempt)
        run.status = RunStatus.running
        await session.flush()
        claimed.append(
            ClaimedRun(
                run_id=run.id,
                attempt_id=attempt.id,
                operation_key=f"{run.operation_namespace}.{run.operation_name}",
                inputs=dict(run.inputs or {}),
                caller=dict(run.caller) if run.caller else None,
            )
        )
    return claimed
