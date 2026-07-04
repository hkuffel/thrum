from __future__ import annotations

import enum


class Trigger(enum.StrEnum):
    schedule = "schedule"
    enqueue = "enqueue"
    workflow = "workflow"


class RunStatus(enum.StrEnum):
    scheduled = "scheduled"
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    missed = "missed"


class EffectKind(enum.StrEnum):
    insert = "insert"
    update = "update"
    delete = "delete"


class AttemptOutcome(enum.StrEnum):
    succeeded = "succeeded"
    failed = "failed"
    timed_out = "timed_out"
    abandoned = "abandoned"
    requeued = "requeued"
