"""thrum.jobs — the durable job slice: cron, queues, and (later) workflows as one
primitive, the Run, backed by your own Postgres.

This is the framework's first transport projection — a job is an `@operation`
projected onto a queue (ADR-0023). The package re-exports only the lightweight SDK
surface (Registry, Operation, transactional enqueue) and must not import the
Worker, scheduler, or server at load time (import-discipline law).
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
