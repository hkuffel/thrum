"""Unit tests for the @operation authoring contract (ADR-0023, PRD-0001).

The keystone slice: a bare `@operation` resolves to identity `default.<fn_name>`,
captures the function signature, and exposes `op.enqueue(session, **inputs)`
that validates the inputs are present at the call (failing fast at enqueue
rather than later in the worker). The integration counterpart (writing a
`pending` Run row to Postgres) lives next to the other ephemeral-Postgres tests
and skips when Docker is unavailable.
"""

from __future__ import annotations

import inspect

import pytest

from thrum import Operation, operation


def test_bare_operation_falls_into_default_namespace() -> None:
    @operation
    def foo() -> None: ...

    assert isinstance(foo, Operation)
    assert foo.namespace == "default"
    assert foo.name == "foo"
    assert foo.key == "default.foo"


def test_operation_name_defaults_to_function_name() -> None:
    @operation
    def send_receipts() -> None: ...

    assert send_receipts.name == "send_receipts"


def test_operation_captures_signature() -> None:
    @operation
    def foo(invoice_id: int, sent_at: str) -> None: ...

    # Signature is what op.enqueue uses to validate inputs at the call.
    assert isinstance(foo.signature, inspect.Signature)
    assert list(foo.signature.parameters) == ["invoice_id", "sent_at"]


def test_operation_is_still_callable() -> None:
    @operation
    def add(a: int, b: int) -> int:
        return a + b

    # The decorator must not break direct calls — useful for unit-testing
    # the function body without booting the queue.
    assert add(2, 3) == 5


def test_op_enqueue_rejects_missing_required_input() -> None:
    @operation
    def send_receipts(invoice_id: int) -> None: ...

    # No real session needed: validation must happen before any session work.
    with pytest.raises(TypeError):
        send_receipts.enqueue(session=None)


def test_op_enqueue_rejects_misspelled_input() -> None:
    @operation
    def send_receipts(invoice_id: int) -> None: ...

    with pytest.raises(TypeError):
        send_receipts.enqueue(session=None, invioce_id=1)
