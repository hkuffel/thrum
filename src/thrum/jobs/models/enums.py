"""Lifecycle and outcome enums. The distinctions here are load-bearing and
documented in CONTEXT.md and the ADRs — do not collapse them casually."""

from __future__ import annotations

import enum


class Trigger(enum.StrEnum):
    """The three ways a Run is created (ADR-0002). `workflow` is reserved for v2
    (ADR-0012) and carries no v1 schema footprint beyond this enum value."""

    schedule = "schedule"
    enqueue = "enqueue"
    workflow = "workflow"


class RunStatus(enum.StrEnum):
    """Run lifecycle. `scheduled` (materialized, not yet claimable) and `pending`
    (claimable now, modulo next_attempt_at) are distinct (Q2 of the core-loop
    grill). `missed` is terminal. `late`/`overrun` are NOT here — they are
    derived flags, not lifecycle states (ADR-0010 / CONTEXT)."""

    scheduled = "scheduled"
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    missed = "missed"


class AttemptOutcome(enum.StrEnum):
    """How an Attempt ended. The taxonomy drives the dashboard's "is this
    alarming?" read (ADR-0016): `abandoned` is a reaped orphan (alarming),
    `requeued` is a clean-drain release on deploy (routine)."""

    succeeded = "succeeded"
    failed = "failed"       # the Task raised
    timed_out = "timed_out"  # hard timeout where enforceable (ADR-0017)
    abandoned = "abandoned"  # reaped orphan — its Worker died (ADR-0013)
    requeued = "requeued"    # released by clean drain on shutdown (ADR-0016)
