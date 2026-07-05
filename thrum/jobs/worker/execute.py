"""The outcome of running one Attempt, handed to result recording."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from thrum.jobs.models import AttemptOutcome


@dataclass(frozen=True)
class ExecutionResult:
    """What an Execution produced, before it is written back to the Run.

    Attributes:
        output: The Operation's return value when it succeeded, else ``None``.
        error: Failure detail when it did not, else ``None``.
    """

    outcome: AttemptOutcome
    output: dict | None
    error: str | None
