"""Lifecycle and outcome enums. The distinctions here are load-bearing and
documented in CONTEXT.md and the ADRs — do not collapse them casually."""

from __future__ import annotations

import enum


class Trigger(enum.StrEnum):
    """The three ways a Run is created. `workflow` is reserved for v2 and carries
    no v1 schema footprint beyond this enum value."""

    schedule = "schedule"
    enqueue = "enqueue"
    workflow = "workflow"


class RunStatus(enum.StrEnum):
    """Run lifecycle. `scheduled` (materialized, not yet claimable) and `pending`
    (claimable now, modulo next_attempt_at) are distinct. `missed` is terminal.
    `late`/`overrun` are NOT here — they are derived flags, not lifecycle states
    (CONTEXT.md)."""

    scheduled = "scheduled"
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    missed = "missed"


class EffectKind(enum.StrEnum):
    """The mutation an Effect records. Table-level only — no read kind, since an
    Effect is what an Attempt changed, not what it observed (ADR-0024)."""

    insert = "insert"
    update = "update"
    delete = "delete"


class AttemptOutcome(enum.StrEnum):
    """How an Attempt ended. The taxonomy drives the dashboard's "is this
    alarming?" read: `abandoned` is a reaped orphan (alarming), `requeued` is a
    clean-drain release on deploy (routine)."""

    succeeded = "succeeded"
    failed = "failed"  # the Operation raised
    timed_out = "timed_out"  # hard timeout where enforceable
    abandoned = "abandoned"  # reaped orphan — its Worker died (ADR-0013)
    requeued = "requeued"  # released by clean drain on shutdown
