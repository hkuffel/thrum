from __future__ import annotations

import enum
import inspect
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any


class ParamKind(enum.Enum):
    REQUIRED_DATA = "required-data"
    OPTIONAL_DATA = "optional-data"
    CAPABILITY = "capability"


@dataclass(frozen=True)
class ClassifiedParam:
    name: str
    kind: ParamKind
    annotation: Any
    default: Any
    metadata: tuple[Any, ...] = ()

    @property
    def has_default(self) -> bool:
        return self.default is not inspect.Parameter.empty


@dataclass(frozen=True)
class InputSchema:
    signature: inspect.Signature
    required: tuple[str, ...]
    optional: tuple[str, ...]

    def bind(self, /, **inputs: Any) -> inspect.BoundArguments:
        return self.signature.bind(**inputs)


@dataclass(frozen=True)
class SignatureModel:
    parameters: tuple[ClassifiedParam, ...]
    input_schema: InputSchema
    output_type: Any

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
    if isinstance(fn, inspect.Signature):
        sig = fn
        resolved_hints: dict[str, Any] = {}
        return_hint: Any = sig.return_annotation
    else:
        sig = inspect.signature(fn)
        try:
            resolved_hints = inspect.get_annotations(fn, eval_str=True)
        except Exception:
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
    metadata = getattr(annotation, "__metadata__", None)
    if metadata is None:
        return annotation, ()
    return annotation.__origin__, tuple(metadata)


def _classify_param(
    param: inspect.Parameter,
    annotation: Any,
    capability_types: frozenset[type],
) -> ParamKind:
    if param.kind is inspect.Parameter.KEYWORD_ONLY:
        if annotation in capability_types:
            return ParamKind.CAPABILITY
        return ParamKind.OPTIONAL_DATA
    return ParamKind.REQUIRED_DATA
