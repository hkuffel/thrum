from __future__ import annotations

import inspect
from datetime import date

import pytest

from thrum.jobs.providers import ReadOnly
from thrum.jobs.signature import (
    InputSchema,
    ParamKind,
    classify,
)


class FakeSession:
    pass


class FakeMailer:
    pass


def test_positional_param_is_required_data() -> None:
    def fn(invoice_id: int) -> None: ...

    model = classify(fn, capability_types=())

    assert [(p.name, p.kind) for p in model.parameters] == [
        ("invoice_id", ParamKind.REQUIRED_DATA),
    ]


def test_positional_only_param_is_required_data() -> None:
    def fn(invoice_id: int, /) -> None: ...

    model = classify(fn, capability_types=())

    assert model.parameters[0].kind is ParamKind.REQUIRED_DATA


def test_keyword_only_non_capability_param_is_optional_data() -> None:
    def fn(*, dry_run: bool = False) -> None: ...

    model = classify(fn, capability_types=())

    assert [(p.name, p.kind) for p in model.parameters] == [
        ("dry_run", ParamKind.OPTIONAL_DATA),
    ]


def test_keyword_only_capability_typed_param_is_capability() -> None:
    def fn(*, db: FakeSession) -> None: ...

    model = classify(fn, capability_types=[FakeSession])

    assert [(p.name, p.kind) for p in model.parameters] == [
        ("db", ParamKind.CAPABILITY),
    ]


def test_keyword_only_capability_with_default_is_still_capability() -> None:

    def fn(*, db: FakeSession = None) -> None: ...

    model = classify(fn, capability_types=[FakeSession])

    assert model.parameters[0].kind is ParamKind.CAPABILITY


def test_multiple_capabilities_each_classified() -> None:
    def fn(*, db: FakeSession, mailer: FakeMailer) -> None: ...

    model = classify(fn, capability_types=[FakeSession, FakeMailer])

    assert [p.kind for p in model.parameters] == [
        ParamKind.CAPABILITY,
        ParamKind.CAPABILITY,
    ]


def test_read_only_marked_capability_unwraps_to_registered_type() -> None:

    def fn(*, db: ReadOnly[FakeSession]) -> None: ...

    model = classify(fn, capability_types=[FakeSession])

    [param] = model.parameters
    assert param.kind is ParamKind.CAPABILITY
    assert param.annotation is FakeSession
    assert ReadOnly in param.metadata


def test_unmarked_capability_carries_no_attenuation_marker() -> None:
    def fn(*, db: FakeSession) -> None: ...

    [param] = classify(fn, capability_types=[FakeSession]).parameters

    assert param.kind is ParamKind.CAPABILITY
    assert param.metadata == ()


def test_trap_keyword_only_optional_non_capability_is_optional_data() -> None:

    def fn(*, since: date | None = None) -> None: ...

    model = classify(fn, capability_types=[FakeSession])

    [(name, kind)] = [(p.name, p.kind) for p in model.parameters]
    assert name == "since"
    assert kind is ParamKind.OPTIONAL_DATA
    assert kind is not ParamKind.CAPABILITY


def test_positional_of_capability_type_is_not_a_capability() -> None:

    def fn(db: FakeSession) -> None: ...

    model = classify(fn, capability_types=[FakeSession])

    assert model.parameters[0].kind is ParamKind.REQUIRED_DATA


def test_empty_capability_registry_makes_every_kwonly_optional_data() -> None:

    def fn(customer_id: int, *, db: FakeSession) -> None: ...

    model = classify(fn, capability_types=())

    assert [(p.name, p.kind) for p in model.parameters] == [
        ("customer_id", ParamKind.REQUIRED_DATA),
        ("db", ParamKind.OPTIONAL_DATA),
    ]


def test_mixed_signature_classifies_all_three_kinds() -> None:
    def send_receipts(
        customer_id: int,
        *,
        since: date | None = None,
        db: FakeSession,
    ) -> dict: ...

    model = classify(send_receipts, capability_types=[FakeSession])

    assert [(p.name, p.kind) for p in model.parameters] == [
        ("customer_id", ParamKind.REQUIRED_DATA),
        ("since", ParamKind.OPTIONAL_DATA),
        ("db", ParamKind.CAPABILITY),
    ]
    assert [p.name for p in model.required_data] == ["customer_id"]
    assert [p.name for p in model.optional_data] == ["since"]
    assert [p.name for p in model.capabilities] == ["db"]


@pytest.mark.parametrize(
    "fn, capability_types, expected",
    [
        (
            lambda x: None,
            (),
            [("x", ParamKind.REQUIRED_DATA)],
        ),
        (
            lambda *, x=1: None,
            (),
            [("x", ParamKind.OPTIONAL_DATA)],
        ),
        (
            lambda a, b, *, c=2: None,
            (),
            [
                ("a", ParamKind.REQUIRED_DATA),
                ("b", ParamKind.REQUIRED_DATA),
                ("c", ParamKind.OPTIONAL_DATA),
            ],
        ),
    ],
    ids=["positional", "kwonly", "mixed"],
)
def test_classification_matrix(fn, capability_types, expected) -> None:
    model = classify(fn, capability_types=capability_types)
    assert [(p.name, p.kind) for p in model.parameters] == expected


def test_input_schema_excludes_capability_params() -> None:

    def fn(customer_id: int, *, since: date | None = None, db: FakeSession) -> None: ...

    model = classify(fn, capability_types=[FakeSession])
    schema = model.input_schema

    assert isinstance(schema, InputSchema)
    assert list(schema.signature.parameters) == ["customer_id", "since"]
    assert schema.required == ("customer_id",)
    assert schema.optional == ("since",)


def test_input_schema_bind_accepts_valid_inputs() -> None:
    def fn(customer_id: int, *, since: date | None = None) -> None: ...

    schema = classify(fn, capability_types=()).input_schema

    bound = schema.bind(customer_id=42, since=None)
    assert bound.arguments == {"customer_id": 42, "since": None}


def test_input_schema_bind_rejects_missing_required() -> None:
    def fn(customer_id: int) -> None: ...

    schema = classify(fn, capability_types=()).input_schema

    with pytest.raises(TypeError):
        schema.bind()


def test_input_schema_bind_rejects_misspelled_kwarg() -> None:
    def fn(customer_id: int) -> None: ...

    schema = classify(fn, capability_types=()).input_schema

    with pytest.raises(TypeError):
        schema.bind(custmer_id=1)


def test_input_schema_bind_rejects_capability_named_input() -> None:

    def fn(*, db: FakeSession) -> None: ...

    schema = classify(fn, capability_types=[FakeSession]).input_schema

    assert list(schema.signature.parameters) == []
    with pytest.raises(TypeError):
        schema.bind(db=FakeSession())


def test_output_type_captured() -> None:
    def fn() -> dict: ...

    model = classify(fn, capability_types=())

    assert model.output_type is dict


def test_output_type_empty_when_no_annotation() -> None:
    def fn(): ...

    model = classify(fn, capability_types=())

    assert model.output_type is inspect.Signature.empty


def test_classify_accepts_a_raw_signature() -> None:

    def fn(x: int) -> None: ...

    sig = inspect.signature(fn)

    model = classify(sig, capability_types=())
    assert model.parameters[0].name == "x"
