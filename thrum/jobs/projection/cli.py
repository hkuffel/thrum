from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any
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
    """Decode argv into the Operation's inputs, run inline through the Execution
    Scope on a system Caller, and encode the output. Creates no Run, Attempt, or
    Effect."""
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
    """Bind argv against the Operation's data-only input schema: bare tokens fill
    required params in order, `--name value` / `--name=value` fill the rest, each
    coerced to its annotated scalar type.

    Classified fresh against the projection's registered `capability_types`, so a
    Capability param (`db`) is stripped from the schema rather than mistaken for an
    input."""
    model = classify(operation.fn, capability_types=capability_types)
    # The classifier's resolved (non-string) annotations drive coercion; the raw
    # signature would carry PEP 563 strings.
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
        inputs[name] = _coerce(value, annotations[name])
    for key, value in options.items():
        inputs[key] = _coerce(value, annotations.get(key, str))

    model.input_schema.bind(**inputs)
    return inputs


def _coerce(value: str, annotation: Any) -> Any:
    if annotation is int:
        return int(value)
    if annotation is float:
        return float(value)
    if annotation is bool:
        return value.lower() in ("1", "true", "yes", "on")
    return value


def encode_json(output: Any) -> str:
    return json.dumps(_to_jsonable(output), indent=2)


def encode_table(output: Any) -> str:
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
    """Normalize any output to a list of row dicts for tabular rendering: a list
    of records stays a list, a lone record becomes one row, a scalar a `value`
    column."""
    jsonable = _to_jsonable(output)
    if jsonable is None:
        return []
    if isinstance(jsonable, list):
        return [row if isinstance(row, dict) else {"value": row} for row in jsonable]
    if isinstance(jsonable, dict):
        return [jsonable]
    return [{"value": jsonable}]


def _to_jsonable(value: Any) -> Any:
    """Reduce read types and stdlib scalars to JSON-native values."""
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
