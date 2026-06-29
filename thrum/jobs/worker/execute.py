"""ExecutionResult — the outcome value the Scope records (ADR-0024).

The pure, out-of-transaction `execute_run` this module once held is replaced by
the Execution Scope's in-transaction invocation (`worker/scope.py`): the Operation
now runs inside one framework-owned transaction with an injected session, so
execution and recording are no longer separable steps. This value object survives
as the shape Record consumes on both the success (in-transaction) and failure
(separate-transaction) paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from thrum.jobs.models import AttemptOutcome


@dataclass(frozen=True)
class ExecutionResult:
    outcome: AttemptOutcome  # succeeded or failed
    output: dict | None
    error: str | None  # traceback on failure
