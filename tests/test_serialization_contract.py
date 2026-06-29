"""Unit tests for runtime serialization-contract enforcement (ADR-0006, ADR-0023).

The static lint (`is_serializable_annotation`, exercised through `app.compile()`)
is covered in `test_app_compile.py`. This file covers the runtime value-level
guard: the same `ensure_serializable` check fires at the queue's input boundary
(`enqueue`), so a live object crossing it fails with one clear, contract-pointing
error rather than a cryptic encoder failure deeper in the transport. The output
boundary is now the Execution Scope's in-transaction check (test_scope.py).

The input boundary is reached without Postgres — the guard raises before the
session is touched.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from thrum.jobs.enqueue import enqueue
from thrum.jobs.serialization import (
    SerializationContractError,
    ensure_serializable,
    is_serializable_annotation,
)


class Customer:
    """A stand-in for a live ORM object — the thing the contract forbids."""


# The shared value-level guard


def test_serializable_scalars_and_containers_pass() -> None:
    ensure_serializable(
        {"id": 1, "when": date(2026, 1, 1), "amount": Decimal("4.20"), "tags": ["a"]},
        owner="default.op",
        role="input 'data'",
    )  # no raise


def test_bare_object_is_rejected_with_contract_pointer() -> None:
    with pytest.raises(SerializationContractError) as exc:
        ensure_serializable(Customer(), owner="default.op", role="output")
    message = str(exc.value)
    assert "default.op" in message
    assert "output is not JSON-serializable" in message
    assert "Customer" in message
    assert "ADR-0006" in message


def test_nested_offender_is_located_by_path() -> None:
    bad = {"order": {"lines": [{"customer": Customer()}]}}
    with pytest.raises(SerializationContractError) as exc:
        ensure_serializable(bad, owner="default.op", role="input 'order'")
    assert "input 'order'.order.lines[0].customer" in str(exc.value)


def test_non_string_dict_key_is_rejected() -> None:
    with pytest.raises(SerializationContractError):
        ensure_serializable({1: "x"}, owner="default.op", role="output")


def test_set_is_rejected_as_non_json() -> None:
    with pytest.raises(SerializationContractError):
        ensure_serializable({1, 2}, owner="default.op", role="output")


# Input boundary: enqueue rejects a non-serializable Data value


def test_enqueue_rejects_non_serializable_input() -> None:
    # The guard fires before the session is used, so a sentinel session is safe.
    with pytest.raises(SerializationContractError, match="input 'customer'"):
        enqueue(object(), "billing.charge", customer=Customer())


def test_enqueue_accepts_serializable_input_up_to_the_db(monkeypatch) -> None:
    """A serializable input clears the guard and reaches the Run insert — proof
    the guard rejects values, not the call itself."""

    class FakeSession:
        def __init__(self) -> None:
            self.added: list = []

        def add(self, obj) -> None:
            self.added.append(obj)

    session = FakeSession()
    run = enqueue(session, "billing.charge", customer_id=7)
    assert session.added == [run]
    assert run.inputs == {"customer_id": 7}


# The static lint reads the same notion of serializability


def test_annotation_lint_agrees_with_runtime_guard() -> None:
    assert is_serializable_annotation(int)
    assert is_serializable_annotation(list[int])
    assert not is_serializable_annotation(Customer)


def test_set_annotation_is_rejected_like_set_values() -> None:
    """JSON has no set type: the runtime guard rejects set values, so the lint
    must reject set/frozenset annotations too — bare and parameterized."""
    with pytest.raises(SerializationContractError):
        ensure_serializable({1, 2}, owner="default.op", role="output")
    assert not is_serializable_annotation(set)
    assert not is_serializable_annotation(set[int])
    assert not is_serializable_annotation(frozenset[int])
