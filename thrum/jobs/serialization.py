from __future__ import annotations

import dataclasses
import datetime as dt
import inspect
import types
import uuid
from decimal import Decimal
from typing import Any, NoReturn, Union, get_args, get_origin, get_type_hints

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
    pass


def ensure_serializable(value: Any, *, owner: str, role: str) -> None:
    _check(value, [], owner, role)


def _check(value: Any, path: list[str], owner: str, role: str) -> None:
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
