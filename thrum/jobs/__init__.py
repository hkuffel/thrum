"""The ``thrum.jobs`` durable-job slice — engine, SDK, and Worker.

Re-exports the authoring and execution primitives so callers reach them by a
single canonical path.
"""

from thrum.jobs.app import App, CompileError
from thrum.jobs.enqueue import enqueue
from thrum.jobs.providers import Caller, Provider, ProviderContext, ReadOnly, db_provider
from thrum.jobs.registry import (
    DeclaredSchedule,
    Operation,
    Registry,
    operation,
)

__all__ = [
    "App",
    "Caller",
    "CompileError",
    "DeclaredSchedule",
    "Operation",
    "Provider",
    "ProviderContext",
    "ReadOnly",
    "Registry",
    "operation",
    "enqueue",
    "db_provider",
]
