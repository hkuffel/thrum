"""The serialization contract: an Operation's Data and output must be
JSON-serializable, IDs not objects (ADR-0006).

`ensure_serializable` guards a boundary at runtime (the value crossing it);
`is_serializable_annotation` is the static counterpart the Compile lint reads.
Both share `_SERIALIZABLE_TYPES`, so the runtime guard and the lint cannot drift.

SDK-core surface: stdlib only, no Worker/server imports (the import-discipline
law).
"""

from __future__ import annotations

import datetime as dt
import inspect
import types
import uuid
from decimal import Decimal
from typing import Any, NoReturn, Union, get_args, get_origin

# JSON-native scalars plus the stdlib types that routinely encode to a JSON
# scalar. The set catches ORM objects and arbitrary classes; it is not a strict
# JSON purist.
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
    """A non-serializable value reached a serialization boundary. The message
    names the offending location and points at ADR-0006."""


def ensure_serializable(value: Any, *, owner: str, role: str) -> None:
    """Guard one serialization boundary. `owner` is the Operation key and `role`
    labels the boundary ("input 'customer'", "output"); on the first
    non-serializable node the error names its path. Returns None when the whole
    value serializes."""
    _check(value, [], owner, role)


def _check(value: Any, path: list[str], owner: str, role: str) -> None:
    """Recurse into `value`, raising at the first non-serializable node. `path`
    is mutated in place rather than copied, so the all-serializable path (the
    common case) allocates nothing."""
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):  # JSON object keys must be strings
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
        # set/frozenset have no JSON form, so the runtime guard rejects their
        # values — the lint must reject the annotation to stay in lockstep.
        if origin in (set, frozenset):
            return False
        if origin in (list, tuple, dict):
            return all(
                is_serializable_annotation(a) for a in get_args(annotation) if a is not Ellipsis
            )
        return True  # other generics — be lenient

    if isinstance(annotation, type):
        # Bare containers (`dict`, `list`, ...) — element types are invisible
        # here, so be lenient; only their concrete elements could fail. A bare
        # set/frozenset falls through to the scalar check, which rejects it.
        if issubclass(annotation, (list, tuple, dict)):
            return True
        return issubclass(annotation, _SERIALIZABLE_TYPES)

    return True  # forward-ref string or unknown object — be lenient
