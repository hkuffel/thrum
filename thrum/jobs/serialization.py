"""The v1 serialization contract for an Operation's Data and output.

Data and output must be addressable — reconstructable on the far side of a
transport's time/process gap. v1 ships only the identity Codec, so that contract
reduces to JSON-of-scalars: this module both checks a runtime value
(``ensure_serializable``) and a static annotation (``is_serializable_annotation``)
against it. Richer Codecs (blob staging, structural) are future work.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import inspect
import types
import uuid
from decimal import Decimal
from typing import Any, NoReturn, Union, get_args, get_origin, get_type_hints

# The scalar leaves the identity Codec accepts. Containers and dataclasses are
# walked structurally down to these; anything else is rejected.
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


class SerializationContractError(TypeError):
    """A value or annotation breaches the JSON serialization contract."""


def ensure_serializable(value: Any, *, owner: str, role: str) -> None:
    """Assert a runtime value satisfies the serialization contract.

    Args:
        owner: The Operation key, named in the error to locate the breach.
        role: What the value is ("input" or "output"), named in the error.

    Raises:
        SerializationContractError: If the value, walked recursively, contains a
            leaf that is not JSON-serializable. The message names the offending
            path within the value.
    """
    _check(value, [], owner, role)


def _check(value: Any, path: list[str], owner: str, role: str) -> None:
    """Recursively walk a value to its leaves, rejecting the first non-scalar.

    ``path`` accumulates the location of the current node (dict keys, list
    indices, dataclass fields) so a rejection can point at exactly where.
    """
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _reject(key, path + [f"[key={key!r}]"], owner, role)
            path.append(f".{key}")
            _check(item, path, owner, role)
            path.pop()
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            path.append(f"[{index}]")
            _check(item, path, owner, role)
            path.pop()
        return
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(value):
            path.append(f".{field.name}")
            _check(getattr(value, field.name), path, owner, role)
            path.pop()
        return
    if value is None or isinstance(value, _SERIALIZABLE_TYPES):
        return
    _reject(value, path, owner, role)


def _reject(offender: Any, path: list[str], owner: str, role: str) -> NoReturn:
    raise SerializationContractError(
        f"{owner}: {role}{''.join(path)} is not JSON-serializable (got "
        f"{type(offender).__name__}). An Operation's Data and output must cross "
        f"every transport's serialization boundary as JSON — pass an ID (e.g. a "
        f"CustomerId newtype), not an object, and re-fetch via the db Capability "
        f"(ADR-0006)."
    )


def is_serializable_annotation(annotation: Any) -> bool:
    """Decide statically whether a type satisfies the serialization contract.

    Used at Compile to reject a non-addressable Data parameter or return type
    before any Run exists. An unannotated or ``None`` type is accepted; a
    ``NewType`` defers to its supertype (so an ``int``-backed ``CustomerId``
    passes). A parameterized container is serializable iff its element types
    are; a bare container without type args is accepted optimistically.

    Returns:
        True if the annotation can be represented as JSON, False otherwise.
    """
    if annotation is inspect.Signature.empty:
        return True
    if annotation is None or annotation is type(None):
        return True

    supertype = getattr(annotation, "__supertype__", None)
    if supertype is not None:
        return is_serializable_annotation(supertype)

    origin = get_origin(annotation)
    if origin is not None:
        if origin in (Union, types.UnionType):
            return all(is_serializable_annotation(a) for a in get_args(annotation))
        if origin in (set, frozenset):
            # JSON has no set type; only a list/tuple/dict survives the boundary.
            return False
        if origin in (list, tuple, dict):
            return all(
                is_serializable_annotation(a) for a in get_args(annotation) if a is not Ellipsis
            )
        return True

    if isinstance(annotation, type):
        if issubclass(annotation, (list, tuple, dict)):
            return True
        if dataclasses.is_dataclass(annotation):
            try:
                hints = get_type_hints(annotation)
            except Exception:
                hints = {}
            return all(
                is_serializable_annotation(hints.get(f.name, f.type))
                for f in dataclasses.fields(annotation)
            )
        return issubclass(annotation, _SERIALIZABLE_TYPES)

    return True
