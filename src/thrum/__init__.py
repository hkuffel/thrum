"""thrum — authoring surface (ADR-0023 / PRD-0001).

A developer authors one thing — an `@operation` — and reaches the queue by
projecting it (`op.enqueue(session, **inputs)`). A bare `@operation` resolves
to identity `default.<fn_name>`; under a named `Registry`, identity inherits
the registry's namespace (`@billing.operation` → `billing.<fn_name>`). A
duplicate `namespace.name` fails fast at decoration/import time.

The `Operation`/`Registry`/bare-`operation` surface lives in
``thrum.jobs.registry``; this top-level module re-exports it as the package's
front door. Later slices fill in the data/capability split, full
`@operation(...)` config, the `op.schedule(...)` projection, and the Compile
finalizer.
"""

from __future__ import annotations

from thrum.jobs.registry import Operation, Registry, operation

__version__ = "0.0.2"

__all__ = ["Operation", "Registry", "operation", "__version__"]
