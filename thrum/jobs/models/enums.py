"""The enumerated vocabularies stored on the durable models."""

from __future__ import annotations

import enum


class Trigger(enum.StrEnum):
    """Which of the three sources created a Run."""

    schedule = "schedule"
    enqueue = "enqueue"
    workflow = "workflow"


class RunStatus(enum.StrEnum):
    """A Run's lifecycle state.

    ``scheduled`` is a pre-materialized occurrence awaiting its Fire Time;
    ``pending`` is claimable now; ``missed`` is the terminal state for an
    occurrence whose Fire Time passed unstarted.
    """

    scheduled = "scheduled"
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    missed = "missed"


class EffectKind(enum.StrEnum):
    """The kind of data mutation an Effect records."""

    insert = "insert"
    update = "update"
    delete = "delete"


class AttemptOutcome(enum.StrEnum):
    """How an Attempt ended, on two axes.

    Operation-attributable outcomes spend the retry budget (``failed``,
    ``timed_out``); Worker-attributable ones do not (``abandoned`` is a reaped
    orphan, ``requeued`` is a clean shutdown release).
    """

    succeeded = "succeeded"
    failed = "failed"
    timed_out = "timed_out"
    abandoned = "abandoned"
    requeued = "requeued"
