"""The CLI projection — reaching an Operation from argv against local Postgres.

Runs an Operation inline through the shared Execution Scope with no server: parse
argv into the Operation's Data inputs, invoke it as the system Caller, and render
the output as a table or JSON. A co-equal projection, not a client of an API.
"""

from __future__ import annotations

import enum
import json
import types
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Union, get_args, get_origin
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from thrum.jobs.db.engine import make_async_engine
from thrum.jobs.providers import Caller
from thrum.jobs.scope import ExecutionScope
from thrum.jobs.signature import ParamKind, classify

if TYPE_CHECKING:
    from thrum.jobs.app import App
    from thrum.jobs.registry import Operation


async def project(
    app: App, operation: Operation, dsn: str, argv: list[str], *, as_json: bool = False
) -> str:
    """Invoke an Operation from CLI arguments and render its output.

    Args:
        argv: The Operation's inputs as CLI tokens (positionals and ``--opt``).
        as_json: Render JSON instead of a text table.

    Returns:
        The rendered output, ready to print.
    """
    inputs = decode_argv(operation, argv, capability_types=frozenset(app.providers))
    engine = make_async_engine(dsn)
    try:
        scope = ExecutionScope(async_sessionmaker(engine, expire_on_commit=False), app.providers)
        output = await scope.invoke(operation, inputs, Caller.system())
    finally:
        await engine.dispose()
    return encode_json(output) if as_json else encode_table(output)


def decode_argv(
    operation: Operation, argv: list[str], *, capability_types: frozenset[type] = frozenset()
) -> dict[str, Any]:
    """Parse CLI tokens into an Operation's Data inputs, coerced to their types.

    Positional tokens fill the required Data params in order; ``--name value``
    and ``--name=value`` fill named params. Values are coerced to the param's
    annotated type, then validated against the input schema.

    Raises:
        TypeError: If an option lacks a value or the inputs fail schema binding.
    """
    model = classify(operation.fn, capability_types=capability_types)
    annotations = {
        p.name: p.annotation for p in model.parameters if p.kind is not ParamKind.CAPABILITY
    }

    positionals: list[str] = []
    options: dict[str, str] = {}
    index = 0
    while index < len(argv):
        token = argv[index]
        if token.startswith("--"):
            key, sep, value = token[2:].partition("=")
            if not sep:
                index += 1
                if index >= len(argv):
                    raise TypeError(f"option --{key} requires a value")
                value = argv[index]
            options[key.replace("-", "_")] = value
        else:
            positionals.append(token)
        index += 1

    inputs: dict[str, Any] = {}
    for name, value in zip(model.input_schema.required, positionals, strict=False):
        inputs[name] = _coerce(name, value, annotations[name])
    for key, value in options.items():
        inputs[key] = _coerce(key, value, annotations.get(key, str))

    model.input_schema.bind(**inputs)
    return inputs


def _coerce(name: str, value: str, annotation: Any) -> Any:
    """Coerce a string token to its annotated scalar type.

    An optional type is coerced to its non-``None`` member. Numeric scalars,
    enums, and ISO 8601 timestamps are parsed; anything else is left as the raw
    string. ``name`` labels the parameter in a parse-failure message.

    Raises:
        ValueError: If ``value`` does not parse as the annotated type.
    """
    if get_origin(annotation) in (Union, types.UnionType):
        for arg in get_args(annotation):
            if arg is not type(None):
                return _coerce(name, value, arg)
    if annotation is int:
        return int(value)
    if annotation is float:
        return float(value)
    if annotation is bool:
        return value.lower() in ("1", "true", "yes", "on")
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        try:
            return annotation(value)
        except ValueError:
            allowed = ", ".join(str(member.value) for member in annotation)
            raise ValueError(f"unknown {name} {value!r}; expected one of: {allowed}") from None
    if annotation in (datetime, date, time):
        try:
            return annotation.fromisoformat(value)
        except ValueError:
            raise ValueError(f"invalid {name} timestamp {value!r}; expected ISO 8601") from None
    return value


def encode_json(output: Any) -> str:
    """Render output as indented JSON."""
    return json.dumps(_to_jsonable(output), indent=2)


def encode_table(output: Any) -> str:
    """Render output as a fixed-width text table, one row per record."""
    rows = _rows(output)
    if not rows:
        return "(no rows)"
    columns = list(rows[0].keys())
    widths = {c: max(len(c), *(len(str(row.get(c, ""))) for row in rows)) for c in columns}
    lines = [
        "  ".join(c.ljust(widths[c]) for c in columns),
        "  ".join("-" * widths[c] for c in columns),
    ]
    lines.extend("  ".join(str(row.get(c, "")).ljust(widths[c]) for c in columns) for row in rows)
    return "\n".join(lines)


def _rows(output: Any) -> list[dict[str, Any]]:
    """Normalize output into a list of row dicts for tabular rendering.

    A scalar or non-dict element becomes a single ``value`` column.
    """
    jsonable = _to_jsonable(output)
    if jsonable is None:
        return []
    if isinstance(jsonable, list):
        return [row if isinstance(row, dict) else {"value": row} for row in jsonable]
    if isinstance(jsonable, dict):
        return [jsonable]
    return [{"value": jsonable}]


def _to_jsonable(value: Any) -> Any:
    """Recursively convert dataclasses, UUIDs, dates, and Decimals to JSON types."""
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _to_jsonable(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {key: _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    return value
