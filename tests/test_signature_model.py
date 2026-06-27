"""Unit tests for the signature model (ADR-0023).

A pure I/O-free deep module: given a function signature and the injected set of
registered capability types, classify each parameter as required-data /
optional-data / capability, derive the operation's input schema, and capture the
output type.

The load-bearing rule under test: a parameter is a Capability iff it is
keyword-only AND its annotated type is a registered capability type — both
necessary. The trap case (`*, since: date | None = None` of a non-capability
type) is optional Data, never a Capability.

Tests drive the classifier directly with a fake capability-type registry; no
Postgres, no decorator, no worker.
"""

from __future__ import annotations

import inspect
from datetime import date

import pytest

from thrum.jobs.signature import (
    InputSchema,
    ParamKind,
    classify,
)


class FakeSession:
    """Stand-in for `sqlalchemy.orm.Session` — a registered capability type."""


class FakeMailer:
    """A second registered capability type, for multi-cap signatures."""


# core classification

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
    """Registered-type membership alone makes a keyword-only param a
    Capability — a default does not flip it back to Data."""

    def fn(*, db: FakeSession = None) -> None: ...  # type: ignore[assignment]

    model = classify(fn, capability_types=[FakeSession])

    assert model.parameters[0].kind is ParamKind.CAPABILITY


def test_multiple_capabilities_each_classified() -> None:
    def fn(*, db: FakeSession, mailer: FakeMailer) -> None: ...

    model = classify(fn, capability_types=[FakeSession, FakeMailer])

    assert [p.kind for p in model.parameters] == [
        ParamKind.CAPABILITY,
        ParamKind.CAPABILITY,
    ]


# the trap case

def test_trap_keyword_only_optional_non_capability_is_optional_data() -> None:
    """The load-bearing rule: `*, since: date | None = None` is
    optional Data, NEVER a Capability — its keyword-only-with-default
    shape mimics an injectable, and only the type-membership check
    guards against the misread."""

    def fn(*, since: date | None = None) -> None: ...

    # Register FakeSession to prove the registry is consulted; `date` is
    # NOT registered, so `since` must stay Data.
    model = classify(fn, capability_types=[FakeSession])

    [(name, kind)] = [(p.name, p.kind) for p in model.parameters]
    assert name == "since"
    assert kind is ParamKind.OPTIONAL_DATA
    assert kind is not ParamKind.CAPABILITY


def test_positional_of_capability_type_is_not_a_capability() -> None:
    """A capability-typed param placed positionally is NOT a Capability —
    keyword-only is a necessary condition. Compile fails this as a structural
    error; the signature module only classifies."""

    def fn(db: FakeSession) -> None: ...

    model = classify(fn, capability_types=[FakeSession])

    assert model.parameters[0].kind is ParamKind.REQUIRED_DATA


def test_empty_capability_registry_makes_every_kwonly_optional_data() -> None:
    """With no registered capability types (the v1 default while the
    capability registry is unpopulated), every keyword-only param —
    including those typed as future capabilities — falls through to
    optional Data. Compile will tighten this once the registry is
    populated."""

    def fn(customer_id: int, *, db: FakeSession) -> None: ...

    model = classify(fn, capability_types=())

    assert [(p.name, p.kind) for p in model.parameters] == [
        ("customer_id", ParamKind.REQUIRED_DATA),
        ("db", ParamKind.OPTIONAL_DATA),
    ]


# realistic mixed signature

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


# input schema

def test_input_schema_excludes_capability_params() -> None:
    """The input schema is the data-only signature — capability params
    are removed so an enqueue caller cannot supply them, and a missing
    capability cannot look like a missing input."""

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
    """Passing a capability's name as an input must fail at the call —
    the input schema doesn't include it, so .bind() rejects it as an
    unexpected kwarg."""

    def fn(*, db: FakeSession) -> None: ...
    schema = classify(fn, capability_types=[FakeSession]).input_schema

    assert list(schema.signature.parameters) == []
    with pytest.raises(TypeError):
        schema.bind(db=FakeSession())


# output type

def test_output_type_captured() -> None:
    def fn() -> dict: ...

    model = classify(fn, capability_types=())

    assert model.output_type is dict


def test_output_type_empty_when_no_annotation() -> None:
    def fn(): ...

    model = classify(fn, capability_types=())

    assert model.output_type is inspect.Signature.empty


# accepts a Signature directly

def test_classify_accepts_a_raw_signature() -> None:
    """Pure module — accepts either a callable or a Signature directly,
    so a caller that already has one need not re-introspect."""

    def fn(x: int) -> None: ...
    sig = inspect.signature(fn)

    model = classify(sig, capability_types=())
    assert model.parameters[0].name == "x"
