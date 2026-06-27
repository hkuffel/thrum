"""App — the projection host and phase-two validation finalizer (ADR-0023 /
PRD-0001).

`App` is distinct from `Registry`: a Registry owns *identity* (it declares the
namespace Operations are authored under); the App owns *transports* and, in
this slice, *finalization*. Naming and reaching stay separate as more
transports arrive.

`app.compile()` is the second phase of two-phase validation. Phase one runs at
decoration (import time): capture the signature, register identity, fail-fast
on collision and cron/tz rules. Phase two can only run once the capability
registry is populated, so it is deferred to startup. Compile resolves each
Operation's capabilities against the registered capability types and fails fast
on the residual checks:

- a capability-typed param placed **positionally** (keyword-only is necessary
  for injection — a positional one is a structural error);
- a keyword-only param with **no default** whose type matches **no registered
  capability** (it looks injectable but nothing can supply it — an optional
  Data flag must carry a default, the `since: date | None = None` trap);
- **non-serializable** Data or output (every transport crosses a serialization
  boundary, ADR-0006).

Compile is **idempotent** — the in-memory validated registry is produced once;
a later call (explicit or the implicit-on-first-start backstop) is a harmless
no-op. It is exposed explicitly as `app.compile()` for tests and tooling, and
`ensure_compiled()` is the seam first app/worker start invokes (the actual
worker-boot wiring is the execution work's seam, out of scope here).

SDK-core surface: stdlib only, no Worker/server imports (the import-discipline
law).
"""

from __future__ import annotations

import datetime as dt
import inspect
import types
import uuid
from decimal import Decimal
from typing import Any, Union, get_args, get_origin

from thrum.jobs.registry import Operation, Registry
from thrum.jobs.signature import ParamKind, classify


class CompileError(Exception):
    """Raised by `app.compile()` when one or more Operations fail phase-two
    validation. Aggregates every problem found so a developer fixes them in one
    pass rather than one boot-crash at a time."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        joined = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"Compile failed with {len(problems)} problem(s):\n{joined}")


class App:
    """The projection host. Owns operation discovery + `compile()`.

    By default it discovers Operations from the process-global registry view
    (every `@operation` / `@registry.operation` in the process). Pass a specific
    `Registry` to scope discovery to that registry's namespace.

    Capability types are registered with `provide(type, provider)`; registering
    a provider for type `T` is exactly what makes `T` a registered capability
    type that `compile()` resolves keyword-only params against.
    """

    def __init__(self, registry: Registry | None = None) -> None:
        self._registry = registry
        self._providers: dict[type, Any] = {}
        self._compiled = False

    def provide(self, capability_type: type, provider: Any) -> None:
        """Register a Provider for a Capability type. Registering type `T` is
        what makes `T` resolvable as a Capability at compile time."""
        self._providers[capability_type] = provider

    @property
    def operations(self) -> dict[str, Operation]:
        """The Operations this App finalizes — the scoped registry's, or the
        process-global view when unscoped."""
        if self._registry is None:
            return dict(Registry._global)
        return {op.key: op for op in self._registry.operations.values()}

    def compile(self) -> None:
        """Resolve capabilities and run the residual fail-fast checks. Raises
        `CompileError` aggregating every problem. Idempotent: once compiled, a
        re-run (explicit or implicit-on-first-start) is a no-op."""
        if self._compiled:
            return

        capability_types = frozenset(self._providers)
        problems: list[str] = []
        for op in self.operations.values():
            problems.extend(_check_operation(op, capability_types))

        if problems:
            raise CompileError(problems)

        self._compiled = True

    def ensure_compiled(self) -> None:
        """The implicit-on-first-start backstop. Identical to `compile()` —
        first app/worker start calls this so the validated registry exists
        before any Run executes."""
        self.compile()


def _check_operation(op: Operation, capability_types: frozenset[type]) -> list[str]:
    """Re-classify one Operation against the populated capability registry and
    collect its phase-two problems. Classifying `op.fn` (not the captured
    Signature) resolves PEP 563 string annotations so type-identity checks
    against the registry work."""
    model = classify(op.fn, capability_types=capability_types)
    problems: list[str] = []

    for param in model.parameters:
        if param.kind is ParamKind.CAPABILITY:
            continue

        # A capability-typed param placed positionally — keyword-only is
        # necessary for injection, so this is a structural error.
        if param.annotation in capability_types:
            problems.append(
                f"{op.key}: capability-typed parameter {param.name!r} must be "
                f"keyword-only to be injected (it is positional)"
            )
            continue

        # A keyword-only param with no default whose type is not registered:
        # it looks injectable but nothing can supply it. An optional Data flag
        # must carry a default (the `since: date | None = None` trap stays Data).
        if param.kind is ParamKind.OPTIONAL_DATA and not param.has_default:
            problems.append(
                f"{op.key}: keyword-only parameter {param.name!r} matches no "
                f"registered capability and has no default — give it a default "
                f"to make it optional Data, or register a provider for its type"
            )
            continue

        if not _is_json_serializable(param.annotation):
            problems.append(
                f"{op.key}: parameter {param.name!r} is typed "
                f"{_render(param.annotation)}, which is not JSON-serializable "
                f"(Data must cross the transport boundary — use an ID newtype)"
            )

    if not _is_json_serializable(model.output_type):
        problems.append(
            f"{op.key}: output type {_render(model.output_type)} is not "
            f"JSON-serializable (Run.output is JSONB)"
        )

    return problems


# JSON-native scalars plus the stdlib types that routinely encode to a JSON
# scalar (ISO strings / numbers). The check exists to catch ORM objects and
# arbitrary classes, not to be a strict JSON purist.
_SERIALIZABLE_TYPES = (
    str,
    int,
    float,
    bool,
    dt.date,
    dt.datetime,
    dt.time,
    dt.timedelta,
    uuid.UUID,
    Decimal,
)


def _is_json_serializable(annotation: Any) -> bool:
    """Best-effort serializability check — we only fail on *known* non-
    serializable annotations (full static proof is not attempted, per ADR-0006).
    Unannotated and unresolvable annotations pass."""
    if annotation is inspect.Signature.empty:
        return True
    if annotation is None or annotation is type(None):
        return True

    supertype = getattr(annotation, "__supertype__", None)
    if supertype is not None:  # NewType — recurse on the wrapped type
        return _is_json_serializable(supertype)

    origin = get_origin(annotation)
    if origin is not None:
        if origin in (Union, types.UnionType):
            return all(_is_json_serializable(a) for a in get_args(annotation))
        if origin in (list, set, frozenset, tuple, dict):
            return all(
                _is_json_serializable(a)
                for a in get_args(annotation)
                if a is not Ellipsis
            )
        return True  # other generics — be lenient

    if isinstance(annotation, type):
        # Bare containers (`dict`, `list`, ...) — contents unparameterized, so
        # be lenient; only their concrete element types could fail, and we
        # can't see them here.
        if issubclass(annotation, (list, set, frozenset, tuple, dict)):
            return True
        return issubclass(annotation, _SERIALIZABLE_TYPES)

    return True  # forward-ref string or unknown object — be lenient


def _render(annotation: Any) -> str:
    return getattr(annotation, "__name__", repr(annotation))
