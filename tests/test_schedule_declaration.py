"""Pure unit tests for Schedule declaration on @registry.operation (issue #3).

No Postgres required — these test the SDK-core validation that fires at
import/declaration time."""

from __future__ import annotations

import datetime as dt

import pytest

from thrum.jobs.registry import DeclaredSchedule, Registry


@pytest.fixture(autouse=True)
def _clean_global_registry():
    """Isolate each test from the process-global registry state."""
    saved = Registry._global.copy()
    saved_schedules = Registry._global_schedules.copy()
    yield
    Registry._global.clear()
    Registry._global.update(saved)
    Registry._global_schedules.clear()
    Registry._global_schedules.update(saved_schedules)


def test_schedule_captures_spec_with_policy_fields() -> None:
    reg = Registry("sched_test_1")

    @reg.operation(
        schedule="0 2 * * *",
        timezone="America/Vancouver",
        sla=dt.timedelta(minutes=30),
        declared_duration=dt.timedelta(minutes=5),
        start_grace=dt.timedelta(minutes=10),
    )
    def nightly_reconcile() -> None: ...

    assert nightly_reconcile.declared_schedule is not None
    s = nightly_reconcile.declared_schedule
    assert s.cron == "0 2 * * *"
    assert s.timezone == "America/Vancouver"
    assert s.sla == dt.timedelta(minutes=30)
    assert s.declared_duration == dt.timedelta(minutes=5)
    assert s.start_grace == dt.timedelta(minutes=10)


def test_no_schedule_leaves_task_enqueue_only() -> None:
    reg = Registry("sched_test_2")

    @reg.operation
    def plain_task() -> None: ...

    assert plain_task.declared_schedule is None


def test_invalid_cron_raises_at_declaration() -> None:
    reg = Registry("sched_test_3")
    with pytest.raises(ValueError, match="Invalid cron expression"):

        @reg.operation(schedule="not a cron")
        def bad_cron() -> None: ...


def test_non_iana_timezone_raises_at_declaration() -> None:
    reg = Registry("sched_test_4")
    with pytest.raises(ValueError, match="Unknown IANA timezone"):

        @reg.operation(schedule="0 2 * * *", timezone="Fake/Zone")
        def bad_tz() -> None: ...


def test_fixed_offset_timezone_raises_at_declaration() -> None:
    reg = Registry("sched_test_5")
    with pytest.raises(ValueError, match="Fixed-offset timezone"):

        @reg.operation(schedule="0 2 * * *", timezone="-07:00")
        def fixed_offset() -> None: ...


def test_positive_fixed_offset_raises() -> None:
    reg = Registry("sched_test_5b")
    with pytest.raises(ValueError, match="Fixed-offset timezone"):

        @reg.operation(schedule="0 2 * * *", timezone="+05:30")
        def positive_offset() -> None: ...


def test_duplicate_schedule_for_same_task_identity_raises() -> None:
    reg = Registry("sched_test_6")

    @reg.operation(name="the_job", schedule="0 2 * * *", timezone="UTC")
    def first() -> None: ...

    with pytest.raises(ValueError, match="Operation identity collision"):

        @reg.operation(name="the_job", schedule="0 3 * * *", timezone="UTC")
        def _second() -> None: ...


def test_schedule_with_utc_default_timezone() -> None:
    reg = Registry("sched_test_7")

    @reg.operation(schedule="*/5 * * * *")
    def every_five() -> None: ...

    assert every_five.declared_schedule is not None
    assert every_five.declared_schedule.timezone == "UTC"


def test_schedule_spec_is_frozen() -> None:
    reg = Registry("sched_test_8")

    @reg.operation(schedule="0 2 * * *", timezone="America/Vancouver")
    def my_task() -> None: ...

    with pytest.raises(AttributeError):
        my_task.declared_schedule.cron = "0 3 * * *"  # type: ignore[misc]


def test_schedule_with_minimal_policy() -> None:
    reg = Registry("sched_test_9")

    @reg.operation(schedule="0 0 * * 0", timezone="Europe/London")
    def weekly() -> None: ...

    s = weekly.declared_schedule
    assert s is not None
    assert s.sla is None
    assert s.declared_duration is None
    assert s.start_grace is None


def test_validation_does_not_import_worker_or_server() -> None:
    import sys

    before_worker = "thrum.jobs.worker" in sys.modules
    before_server = "thrum.jobs.server" in sys.modules

    reg = Registry("sched_test_10")

    @reg.operation(schedule="0 2 * * *", timezone="America/Vancouver")
    def check_imports() -> None: ...

    if not before_worker:
        assert "thrum.jobs.worker" not in sys.modules
    if not before_server:
        assert "thrum.jobs.server" not in sys.modules
