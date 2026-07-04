from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest

from thrum import Operation, Registry, operation


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

    assert isinstance(foo.signature, inspect.Signature)
    assert list(foo.signature.parameters) == ["invoice_id", "sent_at"]


def test_operation_captures_signature_model() -> None:
    from thrum.jobs.signature import ParamKind, SignatureModel

    @operation
    def send_receipts(customer_id: int, *, since: str | None = None) -> dict:
        return {}

    assert isinstance(send_receipts.signature_model, SignatureModel)
    assert [(p.name, p.kind) for p in send_receipts.signature_model.parameters] == [
        ("customer_id", ParamKind.REQUIRED_DATA),
        ("since", ParamKind.OPTIONAL_DATA),
    ]
    assert send_receipts.signature_model.output_type is dict


def test_operation_is_still_callable() -> None:
    @operation
    def add(a: int, b: int) -> int:
        return a + b

    assert add(2, 3) == 5


def test_op_enqueue_rejects_missing_required_input() -> None:
    @operation
    def send_receipts(invoice_id: int) -> None: ...

    with pytest.raises(TypeError):
        send_receipts.enqueue(session=None)


def test_op_enqueue_rejects_misspelled_input() -> None:
    @operation
    def send_receipts(invoice_id: int) -> None: ...

    with pytest.raises(TypeError):
        send_receipts.enqueue(session=None, invioce_id=1)


def test_registry_operation_inherits_namespace() -> None:
    billing = Registry("billing")

    @billing.operation
    def send_receipts() -> None: ...

    assert isinstance(send_receipts, Operation)
    assert send_receipts.namespace == "billing"
    assert send_receipts.name == "send_receipts"
    assert send_receipts.key == "billing.send_receipts"


def test_name_override_replaces_derived_function_name() -> None:
    billing = Registry("billing")

    @billing.operation(name="receipts")
    def _internal_function() -> None: ...

    assert _internal_function.key == "billing.receipts"
    assert _internal_function.name == "receipts"


def test_bare_operation_name_override() -> None:
    @operation(name="receipts")
    def _internal_function() -> None: ...

    assert _internal_function.key == "default.receipts"


def test_same_name_under_different_registries_coexists() -> None:
    billing = Registry("billing")
    marketing = Registry("marketing")

    @billing.operation
    def send_receipts() -> None: ...

    @marketing.operation  # type: ignore[no-redef]
    def send_receipts() -> None: ...  # noqa: F811

    assert Registry._global["billing.send_receipts"].namespace == "billing"
    assert Registry._global["marketing.send_receipts"].namespace == "marketing"


def test_duplicate_identity_within_registry_fails_fast() -> None:
    reg = Registry("billing")

    @reg.operation
    def send_receipts() -> None: ...

    with pytest.raises(ValueError, match="Operation identity collision"):

        @reg.operation(name="send_receipts")
        def _other() -> None: ...


def test_duplicate_identity_across_registries_with_same_namespace_fails_fast() -> None:
    reg_a = Registry("billing")
    reg_b = Registry("billing")

    @reg_a.operation
    def send_receipts() -> None: ...

    with pytest.raises(ValueError, match="Operation identity collision"):

        @reg_b.operation
        def send_receipts() -> None: ...


def test_bare_operation_collision_against_named_default_registry() -> None:
    default = Registry("default")

    @default.operation
    def foo() -> None: ...

    with pytest.raises(ValueError, match="Operation identity collision"):

        @operation
        def foo() -> None: ...


def test_operation_decorator_accepts_retries_param() -> None:
    @operation(retries=3)
    def send_receipts() -> None: ...

    assert send_receipts.retries == 3


def test_operation_decorator_retries_default_is_zero() -> None:
    @operation
    def send_receipts() -> None: ...

    assert send_receipts.retries == 0


def test_operation_retry_policy_converts_retries_to_max_attempts() -> None:

    @operation(retries=2)
    def send_receipts() -> None: ...

    assert send_receipts.retry_policy.max_attempts == 3


def test_operation_decorator_accepts_execution_nature_params() -> None:
    @operation(timeout=30.0, cpu_bound=True)
    def crunch() -> None: ...

    assert crunch.timeout == 30.0
    assert crunch.cpu_bound is True


def test_operation_decorator_accepts_full_retry_curve() -> None:
    @operation(
        retries=5,
        retry_initial_delay=2.0,
        retry_max_delay=120.0,
        retry_backoff_factor=1.5,
        retry_jitter=False,
    )
    def send_receipts() -> None: ...

    assert send_receipts.retries == 5
    p = send_receipts.retry_policy
    assert p.max_attempts == 6
    assert p.initial_delay == 2.0
    assert p.max_delay == 120.0
    assert p.backoff_factor == 1.5
    assert p.jitter is False


def test_op_enqueue_per_call_retries_override_stored_on_run() -> None:

    @operation
    def send_receipts(invoice_id: int) -> None: ...

    mock_session = MagicMock()
    run = send_receipts.enqueue(mock_session, retries=5, invoice_id=42)
    assert run.max_attempts == 6


def test_op_enqueue_no_override_leaves_max_attempts_null() -> None:

    @operation
    def send_receipts(invoice_id: int) -> None: ...

    mock_session = MagicMock()
    run = send_receipts.enqueue(mock_session, invoice_id=42)
    assert run.max_attempts is None


def test_op_enqueue_retries_zero_stored_as_max_attempts_one() -> None:

    @operation
    def send_receipts(invoice_id: int) -> None: ...

    mock_session = MagicMock()
    run = send_receipts.enqueue(mock_session, retries=0, invoice_id=42)
    assert run.max_attempts == 1


def test_op_enqueue_retries_not_treated_as_data_input() -> None:

    @operation
    def send_receipts(invoice_id: int) -> None: ...

    mock_session = MagicMock()
    run = send_receipts.enqueue(mock_session, retries=2, invoice_id=99)
    assert "retries" not in run.inputs


def test_non_durable_direct_call_does_not_apply_durability_config() -> None:

    @operation(retries=10)
    def add(a: int, b: int) -> int:
        return a + b

    result = add(2, 3)
    assert result == 5
