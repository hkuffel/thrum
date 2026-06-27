"""The serialization contract — the single boundary law every transport obeys.

An Operation's Data and output must be JSON-serializable: IDs, not objects
(ADR-0006). Every transport crosses a serialization boundary (queue row, HTTP
body, MCP tool args, CLI argv), so the constraint binds the Operation, not any
one projection. This module is that single definition. Both the runtime boundary
guard (`ensure_serializable`, value-level) and the static Compile lint
(`is_serializable_annotation`, annotation-level) read the same notion of what
serializes, so the two can never drift — enforcement is uniform across the data
and output boundary, not re-derived per projection.

The runtime guard exists so a non-serializable value fails at the boundary it
crosses with an error that points at the contract, rather than surfacing later
as a cryptic encoder error deep in a transport.

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

# JSON-native scalars plus the stdlib types that routinely encode to a JSON
# scalar (ISO strings / numbers). The set exists to catch ORM objects and
# arbitrary classes, not to be a strict JSON purist — and it is shared by the
# runtime guard and the static lint so both agree on what serializes.
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
    """Raised at a serialization boundary when an Operation's Data or output is
    not JSON-serializable. Names the offending location and points at the
    contract (ADR-0006: IDs, not objects) instead of letting a downstream
    encoder failure surface with no contract context."""


def ensure_serializable(value: Any, *, owner: str, role: str) -> None:
    """Guard one serialization boundary. `value` is the Data or output crossing
    it, `owner` is the Operation key, and `role` labels the boundary for the
    error (e.g. "input 'customer'", "output"). Raises
    `SerializationContractError` on the first non-serializable node, naming its
    path; returns None when the whole value serializes."""
    found = _locate(value, [])
    if found is None:
        return

    segments, offender = found
    raise SerializationContractError(
        f"{owner}: {role}{''.join(segments)} is not JSON-serializable (got "
        f"{type(offender).__name__}). An Operation's Data and output must cross "
        f"every transport's serialization boundary as JSON — pass an ID (e.g. a "
        f"CustomerId newtype), not an object, and re-fetch via the db Capability "
        f"(ADR-0006)."
    )


def _locate(value: Any, path: list[str]) -> tuple[list[str], Any] | None:
    """Walk `value`, returning the path segments and offending node of the first
    non-serializable value, or None if all of it serializes. Containers recurse;
    a dict key that isn't a string is itself a JSON violation."""
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                return path + [f"[key={key!r}]"], key
            found = _locate(item, path + [f".{key}"])
            if found is not None:
                return found
        return None
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found = _locate(item, path + [f"[{index}]"])
            if found is not None:
                return found
        return None
    if value is None or isinstance(value, _SERIALIZABLE_TYPES):
        return None
    return path, value


def is_serializable_annotation(annotation: Any) -> bool:
    """Static counterpart to `ensure_serializable`: does this type annotation
    describe a JSON-serializable value? Best-effort — only known non-serializable
    annotations fail; unannotated, forward-ref, and unresolvable annotations
    pass, since full static proof is not attempted (the runtime guard is the
    backstop)."""
    if annotation is inspect.Signature.empty:
        return True
    if annotation is None or annotation is type(None):
        return True

    supertype = getattr(annotation, "__supertype__", None)
    if supertype is not None:  # NewType — recurse on the wrapped type
        return is_serializable_annotation(supertype)

    origin = get_origin(annotation)
    if origin is not None:
        if origin in (Union, types.UnionType):
            return all(is_serializable_annotation(a) for a in get_args(annotation))
        if origin in (list, set, frozenset, tuple, dict):
            return all(
                is_serializable_annotation(a)
                for a in get_args(annotation)
                if a is not Ellipsis
            )
        return True  # other generics — be lenient

    if isinstance(annotation, type):
        # Bare containers (`dict`, `list`, ...) — element types are invisible
        # here, so be lenient; only their concrete elements could fail.
        if issubclass(annotation, (list, set, frozenset, tuple, dict)):
            return True
        return issubclass(annotation, _SERIALIZABLE_TYPES)

    return True  # forward-ref string or unknown object — be lenient
