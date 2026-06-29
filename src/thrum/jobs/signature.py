"""Signature model — the pure, I/O-free module that classifies an Operation's
parameters and derives its data contract (ADR-0023).

Given a signature and the injected set of registered capability types, each
parameter is REQUIRED_DATA (positional — inputs every transport must carry),
CAPABILITY (keyword-only with a type in the registered set — framework-injected),
or OPTIONAL_DATA (any other keyword-only param). The type-membership check is what
separates an optional input from a capability and guards the `*, since: date |
None = None` trap.

The capability set is injected, not discovered here, so v1 callers pass an empty
set and the same path tightens once Compile supplies the real registry. From the
classification this derives the input schema `op.enqueue` validates against, so
bad inputs fail at the call rather than in the Worker.
"""

from __future__ import annotations

import enum
import inspect
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any


class ParamKind(enum.Enum):
    """Which bucket a classified parameter falls into."""

    REQUIRED_DATA = "required-data"
    OPTIONAL_DATA = "optional-data"
    CAPABILITY = "capability"


@dataclass(frozen=True)
class ClassifiedParam:
    """One parameter's classification — its name, kind, and the
    annotation + default carried from the original signature so
    downstream consumers (Compile, serialization lint) need not
    re-introspect."""

    name: str
    kind: ParamKind
    annotation: Any
    default: Any  # `inspect.Parameter.empty` when none

    @property
    def has_default(self) -> bool:
        return self.default is not inspect.Parameter.empty


@dataclass(frozen=True)
class InputSchema:
    """The Operation's data contract — the original signature with
    capability params stripped. `bind(**inputs)` is the validation
    primitive `op.enqueue` calls."""

    signature: inspect.Signature
    required: tuple[str, ...]
    optional: tuple[str, ...]

    def bind(self, /, **inputs: Any) -> inspect.BoundArguments:
        """Validate `**inputs` against the data-only signature.
        Raises `TypeError` on missing required, unexpected kwarg,
        etc. — Python's own argument-binding rules surface the right
        diagnostic without us re-inventing them."""
        return self.signature.bind(**inputs)


@dataclass(frozen=True)
class SignatureModel:
    """Full classification + derived schema for one Operation."""

    parameters: tuple[ClassifiedParam, ...]
    input_schema: InputSchema
    output_type: Any  # `inspect.Signature.empty` when no annotation

    @property
    def required_data(self) -> tuple[ClassifiedParam, ...]:
        return tuple(p for p in self.parameters if p.kind is ParamKind.REQUIRED_DATA)

    @property
    def optional_data(self) -> tuple[ClassifiedParam, ...]:
        return tuple(p for p in self.parameters if p.kind is ParamKind.OPTIONAL_DATA)

    @property
    def capabilities(self) -> tuple[ClassifiedParam, ...]:
        return tuple(p for p in self.parameters if p.kind is ParamKind.CAPABILITY)


def classify(
    fn: Callable[..., Any] | inspect.Signature,
    *,
    capability_types: Collection[type] = (),
) -> SignatureModel:
    """Classify each parameter of `fn` and derive the input schema +
    output type. Pass either a callable (the common case) or an
    already-introspected `inspect.Signature`.

    `capability_types` is the injected set of registered capability
    types — populated by execution-side work. Pass an empty collection
    (the v1 default) and every keyword-only param falls through to
    optional Data.
    """
    if isinstance(fn, inspect.Signature):
        sig = fn
        resolved_hints: dict[str, Any] = {}
        return_hint: Any = sig.return_annotation
    else:
        sig = inspect.signature(fn)
        # `from __future__ import annotations` (PEP 563) stringifies every
        # annotation; resolve them so type-identity checks against the capability
        # registry work. Unresolvable forward refs fall back to the raw annotation,
        # and Compile surfaces the mismatch with a clear error.
        try:
            resolved_hints = inspect.get_annotations(fn, eval_str=True)
        except Exception:
            resolved_hints = {}
        return_hint = resolved_hints.get("return", sig.return_annotation)

    cap_set = frozenset(capability_types)

    classified: list[ClassifiedParam] = []
    data_params: list[inspect.Parameter] = []
    for name, param in sig.parameters.items():
        annotation = resolved_hints.get(name, param.annotation)
        kind = _classify_param(param, annotation, cap_set)
        classified.append(
            ClassifiedParam(
                name=name,
                kind=kind,
                annotation=annotation,
                default=param.default,
            )
        )
        if kind is not ParamKind.CAPABILITY:
            data_params.append(param)

    data_signature = sig.replace(parameters=data_params)
    required = tuple(p.name for p in classified if p.kind is ParamKind.REQUIRED_DATA)
    optional = tuple(p.name for p in classified if p.kind is ParamKind.OPTIONAL_DATA)
    input_schema = InputSchema(
        signature=data_signature,
        required=required,
        optional=optional,
    )
    return SignatureModel(
        parameters=tuple(classified),
        input_schema=input_schema,
        output_type=return_hint,
    )


def _classify_param(
    param: inspect.Parameter,
    annotation: Any,
    capability_types: frozenset[type],
) -> ParamKind:
    if param.kind is inspect.Parameter.KEYWORD_ONLY:
        if annotation in capability_types:
            return ParamKind.CAPABILITY
        return ParamKind.OPTIONAL_DATA
    # Positional-only and positional-or-keyword are required data. *args/**kwargs
    # are out of scope for v1 — they fall through here as required data; Compile
    # can reject them with a structural error if needed.
    return ParamKind.REQUIRED_DATA
