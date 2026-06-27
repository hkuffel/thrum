"""thrum — authoring surface (ADR-0023).

A developer authors one thing — an `@operation` — and reaches the queue by
projecting it (`op.enqueue(session, **inputs)`). A bare `@operation` resolves
to identity `default.<fn_name>`; under a named `Registry`, identity inherits
the registry's namespace (`@billing.operation` → `billing.<fn_name>`). A
duplicate `namespace.name` fails fast at decoration/import time.

The `Operation`/`Registry`/bare-`operation` surface lives in
`thrum.jobs.registry` and the `App` finalizer in `thrum.jobs.app`; this
top-level module re-exports them as the package's front door.
"""

from __future__ import annotations

from thrum.jobs.app import App as App, CompileError as CompileError
from thrum.jobs.registry import Operation as Operation, Registry as Registry, operation as operation

__version__ = "0.0.2"

__all__ = ["App", "CompileError", "Operation", "Registry", "operation", "__version__"]
