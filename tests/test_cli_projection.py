"""Unit tests for the generic CLI projection and the RunView read type (ADR-0028).

The projection is `argv -> input schema -> scope.invoke -> encode`; this file
covers the transport-mechanical halves that need no Postgres — argv decoding
against an Operation's input schema, and table/JSON encoding — plus RunView's
reduction to a JSON-able dict. The scope.invoke middle is exercised end-to-end in
test_control_plane.py.
"""

from __future__ import annotations

import json

import pytest

from factories import make_operation
from thrum.jobs.builtins import RunView
from thrum.jobs.projection.cli import _to_jsonable, decode_argv, encode_json, encode_table


def _fake_op(fn):
    return make_operation(fn=fn, namespace="fake", name="op")


# argv decode against the input schema


def test_decode_binds_positional_required_and_typed_options() -> None:
    def fn(name: str, *, limit: int = 10, verbose: bool = False) -> dict: ...

    inputs = decode_argv(_fake_op(fn), ["alice", "--limit", "5", "--verbose", "true"])

    assert inputs == {"name": "alice", "limit": 5, "verbose": True}


def test_decode_accepts_equals_form_and_dashed_option_names() -> None:
    def fn(*, max_rows: int = 100) -> dict: ...

    assert decode_argv(_fake_op(fn), ["--max-rows=42"]) == {"max_rows": 42}


def test_decode_no_argv_binds_empty_inputs() -> None:
    def fn() -> dict: ...

    assert decode_argv(_fake_op(fn), []) == {}


def test_decode_rejects_missing_required_via_bind() -> None:
    def fn(name: str) -> dict: ...

    with pytest.raises(TypeError):
        decode_argv(_fake_op(fn), [])


def test_decode_rejects_unknown_flag_via_bind() -> None:
    def fn(name: str) -> dict: ...

    with pytest.raises(TypeError):
        decode_argv(_fake_op(fn), ["alice", "--nope", "x"])


def test_decode_rejects_trailing_flag_without_value() -> None:
    def fn(limit: int = 0) -> dict: ...

    with pytest.raises(TypeError, match="--limit requires a value"):
        decode_argv(_fake_op(fn), ["--limit"])


# encoding


def test_encode_table_renders_records_as_columns() -> None:
    output = [{"id": "1", "status": "succeeded"}, {"id": "2", "status": "failed"}]

    table = encode_table(output)

    lines = table.splitlines()
    assert lines[0].split() == ["id", "status"]
    assert "succeeded" in lines[2]
    assert "failed" in lines[3]


def test_encode_table_empty_output_is_explicit() -> None:
    assert encode_table([]) == "(no rows)"


def test_encode_json_is_valid_json_of_records() -> None:
    output = [{"id": "1", "status": "succeeded"}]

    assert json.loads(encode_json(output)) == output


def test_encode_reduces_run_view_dataclasses() -> None:
    views = [
        RunView(
            id="a",
            operation="thrum.list_runs",
            status="succeeded",
            trigger="enqueue",
            created_at="2026-07-03T00:00:00+00:00",
        )
    ]

    assert json.loads(encode_json(views)) == [_to_jsonable(views[0])]
    assert "thrum.list_runs" in encode_table(views)


# RunView is a serialization-contract-conformant read type


def test_run_view_reduces_to_a_jsonable_dict() -> None:
    view = RunView(
        id="00000000-0000-0000-0000-000000000000",
        operation="thrum.list_runs",
        status="pending",
        trigger="enqueue",
        created_at="2026-07-03T12:00:00+00:00",
    )

    reduced = _to_jsonable(view)

    assert json.loads(json.dumps(reduced)) == reduced
    assert reduced["operation"] == "thrum.list_runs"
