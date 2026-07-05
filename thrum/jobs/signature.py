"""Classification of an Operation's parameters into Data and Capabilities.

Splits an Operation's signature along the load-bearing axis of the operation
model: positional parameters are Data (typed inputs that cross a transport
boundary), keyword-only parameters are Capabilities (effect-bearing handles the
framework injects at execution). The classification feeds Compile, drives the
input schema that Enqueue validates against, and separates the params a caller
supplies from the ones a Provider constructs.
"""

from __future__ import annotations

import enum
import inspect
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any


class ParamKind(enum.Enum):
    """How a single Operation parameter is reached at execution."""

    REQUIRED_DATA = "required-data"
    OPTIONAL_DATA = "optional-data"
    CAPABILITY = "capability"


@dataclass(frozen=True)
class ClassifiedParam:
    """One Operation parameter tagged with its classification.

    Attributes:
        annotation: The parameter type with any ``Annotated`` metadata stripped,
            so the bare capability type is exposed for Provider resolution.
        metadata: The ``Annotated`` metadata peeled off ``annotation`` — where
            Attenuation markers such as ``ReadOnly`` are carried.
    """

    name: str
    kind: ParamKind
    annotation: Any
    default: Any
    metadata: tuple[Any, ...] = ()

    @property
    def has_default(self) -> bool:
        """Whether the parameter declares a default value."""
        return self.default is not inspect.Parameter.empty


@dataclass(frozen=True)
class InputSchema:
    """The Data-only projection of an Operation's signature.

    Capabilities are excluded: this is the shape a caller supplies across a
    transport boundary, against which Enqueue validates inputs.

    Attributes:
        signature: The signature reduced to its Data parameters.
        required: Names of the required Data parameters.
        optional: Names of the Data parameters that carry a default.
    """

    signature: inspect.Signature
    required: tuple[str, ...]
    optional: tuple[str, ...]

    def bind(self, /, **inputs: Any) -> inspect.BoundArguments:
        """Bind supplied inputs to the Data parameters, raising on a mismatch."""
        return self.signature.bind(**inputs)


@dataclass(frozen=True)
class SignatureModel:
    """The full classification of an Operation's signature.

    Attributes:
        parameters: Every parameter in declaration order, each classified.
        input_schema: The Data-only view used to validate and bind inputs.
        output_type: The Operation's resolved return annotation.
    """

    parameters: tuple[ClassifiedParam, ...]
    input_schema: InputSchema
    output_type: Any

    @property
    def required_data(self) -> tuple[ClassifiedParam, ...]:
        """The Data parameters that must be supplied."""
        return tuple(p for p in self.parameters if p.kind is ParamKind.REQUIRED_DATA)

    @property
    def optional_data(self) -> tuple[ClassifiedParam, ...]:
        """The Data parameters that carry a default."""
        return tuple(p for p in self.parameters if p.kind is ParamKind.OPTIONAL_DATA)

    @property
    def capabilities(self) -> tuple[ClassifiedParam, ...]:
        """The parameters resolved as injected Capabilities."""
        return tuple(p for p in self.parameters if p.kind is ParamKind.CAPABILITY)


def classify(
    fn: Callable[..., Any] | inspect.Signature,
    *,
    capability_types: Collection[type] = (),
) -> SignatureModel:
    """Classify a callable's parameters into Data and Capabilities.

    Args:
        fn: The Operation function to inspect, or a pre-built signature. Passing
            the function lets annotations be resolved from strings; a bare
            signature is taken as-is with no resolution.
        capability_types: The registered capability types. A keyword-only
            parameter is a Capability only if its type is one of these;
            otherwise it is optional Data.

    Returns:
        The classified signature, with a Data-only input schema and the
        resolved output type.
    """
    if isinstance(fn, inspect.Signature):
        sig = fn
        resolved_hints: dict[str, Any] = {}
        return_hint: Any = sig.return_annotation
    else:
        sig = inspect.signature(fn)
        try:
            resolved_hints = inspect.get_annotations(fn, eval_str=True)
        except Exception:
            # An annotation that references an unimported name (a forward ref)
            # cannot be evaluated here; fall back to the raw signature strings.
            resolved_hints = {}
        return_hint = resolved_hints.get("return", sig.return_annotation)

    cap_set = frozenset(capability_types)

    classified: list[ClassifiedParam] = []
    data_params: list[inspect.Parameter] = []
    for name, param in sig.parameters.items():
        registered_type, metadata = _unwrap_annotated(resolved_hints.get(name, param.annotation))
        kind = _classify_param(param, registered_type, cap_set)
        classified.append(
            ClassifiedParam(
                name=name,
                kind=kind,
                annotation=registered_type,
                default=param.default,
                metadata=metadata,
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


def _unwrap_annotated(annotation: Any) -> tuple[Any, tuple[Any, ...]]:
    """Split an ``Annotated`` type into its base type and metadata.

    Returns:
        A tuple ``(base, metadata)``. For a plain (non-``Annotated``) type,
        ``base`` is the type unchanged and ``metadata`` is empty.
    """
    metadata = getattr(annotation, "__metadata__", None)
    if metadata is None:
        return annotation, ()
    return annotation.__origin__, tuple(metadata)


def _classify_param(
    param: inspect.Parameter,
    annotation: Any,
    capability_types: frozenset[type],
) -> ParamKind:
    """Classify one parameter by its position and resolved type.

    Positional parameters are required Data. Keyword-only parameters whose type
    is a registered Capability are Capabilities; any other keyword-only
    parameter is optional Data.
    """
    if param.kind is inspect.Parameter.KEYWORD_ONLY:
        if annotation in capability_types:
            return ParamKind.CAPABILITY
        return ParamKind.OPTIONAL_DATA
    return ParamKind.REQUIRED_DATA
