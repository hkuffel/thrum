from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from thrum.jobs.models import AttemptOutcome


@dataclass(frozen=True)
class ExecutionResult:
    outcome: AttemptOutcome
    output: dict | None
    error: str | None
