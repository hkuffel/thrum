"""SQLAlchemy table definitions for the `thrum` schema — the shared truth."""

from thrum.jobs.models.attempt import Attempt
from thrum.jobs.models.base import SCHEMA, Base
from thrum.jobs.models.enums import AttemptOutcome, RunStatus, Trigger
from thrum.jobs.models.run import Run
from thrum.jobs.models.schedule import Schedule

__all__ = [
    "Base",
    "SCHEMA",
    "Schedule",
    "Run",
    "Attempt",
    "Trigger",
    "RunStatus",
    "AttemptOutcome",
]
