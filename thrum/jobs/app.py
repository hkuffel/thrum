from __future__ import annotations

from typing import Any

from thrum.jobs.registry import Operation, Registry
from thrum.jobs.serialization import is_serializable_annotation
from thrum.jobs.signature import ParamKind, classify


class CompileError(Exception):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        joined = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"Compile failed with {len(problems)} problem(s):\n{joined}")


class App:
    def __init__(self, registry: Registry | None = None) -> None:
        self._registry = registry
        self._providers: dict[type, Any] = {}
        self._compiled = False

    def provide(self, capability_type: type, provider: Any) -> None:
        self._providers[capability_type] = provider

    @property
    def providers(self) -> dict[type, Any]:
        return dict(self._providers)

    @property
    def operations(self) -> dict[str, Operation]:
        if self._registry is None:
            return dict(Registry._global)
        return {op.key: op for op in self._registry.operations.values()}

    def compile(self) -> None:
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
        self.compile()


def _check_operation(op: Operation, capability_types: frozenset[type]) -> list[str]:
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
    return getattr(annotation, "__name__", repr(annotation))
