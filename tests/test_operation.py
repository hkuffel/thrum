"""Unit tests for the @operation authoring contract (ADR-0023, PRD-0001).

The keystone slice: a bare `@operation` resolves to identity `default.<fn_name>`,
captures the function signature, and exposes `op.enqueue(session, **inputs)`
that validates the inputs are present at the call (failing fast at enqueue
rather than later in the worker). The integration counterpart (writing a
`pending` Run row to Postgres) lives next to the other ephemeral-Postgres tests
and skips when Docker is unavailable.

Issue #4 grows this with the named-`Registry` story: `@registry.operation`
inherits the registry's namespace, `name=` overrides the derived name, and a
duplicate `namespace.name` raises immediately so the misconfiguration cannot
reach the Worker.
"""

from __future__ import annotations

import inspect

import pytest

from thrum import Operation, Registry, operation


@pytest.fixture(autouse=True)
def _clean_global_registry():
    """The Registry's process-global view bleeds between tests (bare
    `@operation` now registers under `default` too) — isolate it so each test
    gets a clean slate."""
    saved_ops = Registry._global.copy()
    saved_schedules = Registry._global_schedules.copy()
    Registry._global.clear()
    Registry._global_schedules.clear()
    try:
        yield
    finally:
        Registry._global.clear()
        Registry._global.update(saved_ops)
        Registry._global_schedules.clear()
        Registry._global_schedules.update(saved_schedules)


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


def test_operation_captures_signature_model() -> None:
    """The Operation owns a classified SignatureModel — its data
    contract — alongside the raw signature. With v1's empty capability
    registry, every keyword-only param is optional Data."""
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


# --- Named Registry (issue #4) --------------------------------------------


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
    """The point of namespaces: `billing.send_receipts` and
    `marketing.send_receipts` are distinct identities and must register
    side-by-side without colliding."""
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
    """Two `Registry("billing")` declarations share the namespace; if both
    register `send_receipts`, the second must fail at decoration time."""
    reg_a = Registry("billing")
    reg_b = Registry("billing")

    @reg_a.operation
    def send_receipts() -> None: ...

    with pytest.raises(ValueError, match="Operation identity collision"):

        @reg_b.operation
        def send_receipts() -> None: ...  # noqa: F811


def test_bare_operation_collision_against_named_default_registry() -> None:
    """A bare `@operation def foo` and `Registry("default").operation`-on-`foo`
    must collide — the bare path resolves through the same default
    registry."""
    default = Registry("default")

    @default.operation
    def foo() -> None: ...

    with pytest.raises(ValueError, match="Operation identity collision"):

        @operation
        def foo() -> None: ...  # noqa: F811
