"""The App and Compile — phase two of Operation validation.

The App owns transports and Providers; Compile resolves each Operation's
Capabilities against the registered Providers and fully checks its signature,
failing fast with every problem at once. It is idempotent and runs implicitly on
first start, or explicitly via ``compile()``.
"""

from __future__ import annotations

from typing import Any

from thrum.jobs.registry import Operation, Registry
from thrum.jobs.serialization import is_serializable_annotation
from thrum.jobs.signature import ParamKind, classify


class CompileError(Exception):
    """Compile found one or more Operations that cannot be run.

    Attributes:
        problems: One message per distinct fault, across all Operations.
    """

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        joined = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"Compile failed with {len(problems)} problem(s):\n{joined}")


class App:
    """The projection host that wires Operations onto transports and Providers.

    Registering a Provider for a type is what makes that type a Capability the
    signature classifier will resolve against at Compile.
    """

    def __init__(self, registry: Registry | None = None) -> None:
        self._registry = registry
        self._providers: dict[type, Any] = {}
        self._compiled = False

    def provide(self, capability_type: type, provider: Any) -> None:
        """Register the Provider that constructs a Capability of ``capability_type``."""
        self._providers[capability_type] = provider

    @property
    def providers(self) -> dict[type, Any]:
        """A copy of the registered Providers keyed by capability type."""
        return dict(self._providers)

    @property
    def operations(self) -> dict[str, Operation]:
        """The Operations in scope, keyed by identity.

        Bound to a single Registry when given one; otherwise every Operation
        registered process-wide.
        """
        if self._registry is None:
            return dict(Registry._global)
        return {op.key: op for op in self._registry.operations.values()}

    def compile(self) -> None:
        """Validate every Operation against the registered Providers.

        Idempotent: a second call after success is a no-op.

        Raises:
            CompileError: If any Operation has an unresolvable Capability, a
                misplaced parameter, or a non-serializable Data or output type.
        """
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
        """Compile on first start, the guaranteed backstop before execution."""
        self.compile()


def _check_operation(op: Operation, capability_types: frozenset[type]) -> list[str]:
    """Collect every Compile problem for one Operation.

    Returns:
        A message per fault: a capability-typed param placed positionally, a
        keyword-only param matching no Provider and lacking a default, or a
        non-serializable Data or output type.
    """
    model = classify(op.fn, capability_types=capability_types)
    problems: list[str] = []

    for param in model.parameters:
        if param.kind is ParamKind.CAPABILITY:
            continue

        if param.annotation in capability_types:
            problems.append(
                f"{op.key}: capability-typed parameter {param.name!r} must be "
                f"keyword-only to be injected (it is positional)"
            )
            continue

        if param.kind is ParamKind.OPTIONAL_DATA and not param.has_default:
            problems.append(
                f"{op.key}: keyword-only parameter {param.name!r} matches no "
                f"registered capability and has no default — give it a default "
                f"to make it optional Data, or register a provider for its type"
            )
            continue

        if not is_serializable_annotation(param.annotation):
            problems.append(
                f"{op.key}: parameter {param.name!r} is typed "
                f"{_render(param.annotation)}, which is not JSON-serializable "
                f"(Data must cross the transport boundary — use an ID newtype)"
            )

    if not is_serializable_annotation(model.output_type):
        problems.append(
            f"{op.key}: output type {_render(model.output_type)} is not "
            f"JSON-serializable (Run.output is JSONB)"
        )

    return problems


def _render(annotation: Any) -> str:
    """Render a type for an error message, preferring its bare name."""
    return getattr(annotation, "__name__", repr(annotation))
