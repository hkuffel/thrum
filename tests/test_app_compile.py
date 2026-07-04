from __future__ import annotations

from datetime import date
from typing import NewType

import pytest

from thrum import App, CompileError, Registry, operation


class FakeSession:
    pass


class NotSerializable:
    pass


CustomerId = NewType("CustomerId", int)


def _app_with_db() -> App:
    app = App()
    app.provide(FakeSession, object())
    return app


def test_app_discovers_global_operations() -> None:
    @operation
    def foo() -> None: ...

    app = App()
    assert "default.foo" in app.operations
    assert app.operations["default.foo"] is foo


def test_app_scoped_to_a_registry_sees_only_its_operations() -> None:
    billing = Registry("billing")

    @billing.operation
    def charge() -> None: ...

    @operation
    def bare() -> None: ...

    app = App(registry=billing)
    assert set(app.operations) == {"billing.charge"}


def test_compile_passes_clean_operation() -> None:
    @operation
    def send_receipts(
        customer_id: CustomerId, *, since: date | None = None, db: FakeSession
    ) -> dict: ...

    _app_with_db().compile()


def test_compile_resolves_capability_against_registered_type() -> None:

    @operation
    def op(order_id: int, *, db: FakeSession) -> None: ...

    _app_with_db().compile()


def test_compile_fails_on_positional_capability() -> None:
    @operation
    def op(db: FakeSession) -> None: ...

    with pytest.raises(CompileError, match="must be keyword-only"):
        _app_with_db().compile()


def test_compile_fails_on_unresolved_injectable() -> None:

    @operation
    def op(order_id: int, *, mailer: NotSerializable) -> None: ...

    with pytest.raises(CompileError, match="matches no registered capability"):
        _app_with_db().compile()


def test_trap_keyword_only_optional_data_is_not_an_unresolved_injectable() -> None:

    @operation
    def op(order_id: int, *, since: date | None = None) -> None: ...

    _app_with_db().compile()


def test_compile_fails_on_non_serializable_data() -> None:
    @operation
    def op(thing: NotSerializable) -> None: ...

    with pytest.raises(CompileError, match="not JSON-serializable"):
        _app_with_db().compile()


def test_compile_fails_on_non_serializable_output() -> None:
    @operation
    def op(order_id: int) -> NotSerializable: ...

    with pytest.raises(CompileError, match="output type"):
        _app_with_db().compile()


def test_compile_accepts_newtype_and_container_data() -> None:
    @operation
    def op(customer_id: CustomerId, order_ids: list[int]) -> dict[str, int]: ...

    _app_with_db().compile()


def test_compile_aggregates_all_problems() -> None:
    @operation
    def op(db: FakeSession, thing: NotSerializable) -> NotSerializable: ...

    with pytest.raises(CompileError) as exc:
        _app_with_db().compile()
    assert len(exc.value.problems) == 3


def test_compile_is_idempotent() -> None:
    @operation
    def op(order_id: int, *, db: FakeSession) -> None: ...

    app = _app_with_db()
    app.compile()
    app.compile()


def test_implicit_first_start_rejects_what_explicit_compile_rejects() -> None:

    @operation
    def op(db: FakeSession) -> None: ...

    with pytest.raises(CompileError):
        _app_with_db().compile()
    with pytest.raises(CompileError):
        _app_with_db().ensure_compiled()


def test_ensure_compiled_is_a_noop_after_explicit_compile() -> None:
    @operation
    def op(order_id: int, *, db: FakeSession) -> None: ...

    app = _app_with_db()
    app.compile()
    app.ensure_compiled()
