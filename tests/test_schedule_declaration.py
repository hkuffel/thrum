"""Unit tests for op.schedule(cron, tz=...) declaration.

No Postgres required — these test SDK-core validation that fires at
import/declaration time."""

from __future__ import annotations

import datetime as dt

import pytest

from thrum.jobs.registry import Registry


@pytest.fixture(autouse=True)
def _clean_global_registry():
    saved = Registry._global.copy()
    saved_schedules = Registry._global_schedules.copy()
    yield
    Registry._global.clear()
    Registry._global.update(saved)
    Registry._global_schedules.clear()
    Registry._global_schedules.update(saved_schedules)


def test_schedule_records_declared_schedule() -> None:
    reg = Registry("sched_test_1")

    @reg.operation
    def nightly_reconcile() -> None: ...

    s = nightly_reconcile.schedule(
        "0 2 * * *",
        tz="America/Vancouver",
        sla=dt.timedelta(minutes=30),
        declared_duration=dt.timedelta(minutes=5),
        start_grace=dt.timedelta(minutes=10),
    )

    assert len(nightly_reconcile.declared_schedules) == 1
    assert s is nightly_reconcile.declared_schedules[0]
    assert s.cron == "0 2 * * *"
    assert s.timezone == "America/Vancouver"
    assert s.sla == dt.timedelta(minutes=30)
    assert s.declared_duration == dt.timedelta(minutes=5)
    assert s.start_grace == dt.timedelta(minutes=10)


def test_no_schedule_leaves_list_empty() -> None:
    reg = Registry("sched_test_2")

    @reg.operation
    def plain_task() -> None: ...

    assert plain_task.declared_schedules == []


def test_many_schedules_per_operation() -> None:
    reg = Registry("sched_test_2b")

    @reg.operation
    def multi() -> None: ...

    multi.schedule("0 2 * * *", tz="UTC")
    multi.schedule("0 9 * * 1", tz="America/New_York")

    assert len(multi.declared_schedules) == 2
    assert multi.declared_schedules[0].cron == "0 2 * * *"
    assert multi.declared_schedules[1].cron == "0 9 * * 1"


def test_duplicate_cron_raises_at_declaration() -> None:
    reg = Registry("sched_test_dup")

    @reg.operation
    def dup_op() -> None: ...

    dup_op.schedule("0 2 * * *", tz="UTC")

    with pytest.raises(ValueError, match="Duplicate schedule cron"):
        dup_op.schedule("0 2 * * *", tz="UTC")

    assert len(dup_op.declared_schedules) == 1


def test_duplicate_cron_across_timezones_raises() -> None:
    reg = Registry("sched_test_dup_tz")

    @reg.operation
    def dup_tz_op() -> None: ...

    dup_tz_op.schedule("0 2 * * *", tz="UTC")

    # uq_schedules_task_cron keys on cron alone, so a differing tz does not
    # escape the collision.
    with pytest.raises(ValueError, match="Duplicate schedule cron"):
        dup_tz_op.schedule("0 2 * * *", tz="America/New_York")

    assert len(dup_tz_op.declared_schedules) == 1


def test_invalid_cron_raises_at_declaration() -> None:
    reg = Registry("sched_test_3")

    @reg.operation
    def bad_op() -> None: ...

    with pytest.raises(ValueError, match="Invalid cron expression"):
        bad_op.schedule("not a cron")


def test_non_iana_timezone_raises_at_declaration() -> None:
    reg = Registry("sched_test_4")

    @reg.operation
    def bad_tz_op() -> None: ...

    with pytest.raises(ValueError, match="Unknown IANA timezone"):
        bad_tz_op.schedule("0 2 * * *", tz="Fake/Zone")


def test_fixed_offset_timezone_raises_at_declaration() -> None:
    reg = Registry("sched_test_5")

    @reg.operation
    def fixed_neg() -> None: ...

    with pytest.raises(ValueError, match="Fixed-offset timezone"):
        fixed_neg.schedule("0 2 * * *", tz="-07:00")


def test_positive_fixed_offset_raises() -> None:
    reg = Registry("sched_test_5b")

    @reg.operation
    def fixed_pos() -> None: ...

    with pytest.raises(ValueError, match="Fixed-offset timezone"):
        fixed_pos.schedule("0 2 * * *", tz="+05:30")


def test_schedule_defaults_to_utc() -> None:
    reg = Registry("sched_test_7")

    @reg.operation
    def every_five() -> None: ...

    s = every_five.schedule("*/5 * * * *")

    assert s.timezone == "UTC"


def test_schedule_is_frozen() -> None:
    reg = Registry("sched_test_8")

    @reg.operation
    def my_task() -> None: ...

    s = my_task.schedule("0 2 * * *", tz="America/Vancouver")

    with pytest.raises(AttributeError):
        s.cron = "0 3 * * *"  # type: ignore[misc]


def test_schedule_with_minimal_policy() -> None:
    reg = Registry("sched_test_9")

    @reg.operation
    def weekly() -> None: ...

    s = weekly.schedule("0 0 * * 0", tz="Europe/London")

    assert s.sla is None
    assert s.declared_duration is None
    assert s.start_grace is None


def test_schedule_registers_into_global_schedules() -> None:
    reg = Registry("sched_test_10")

    @reg.operation
    def reconciled() -> None: ...

    reconciled.schedule("0 2 * * *", tz="UTC")

    key = "sched_test_10.reconciled"
    assert key in Registry._global_schedules
    assert len(Registry._global_schedules[key]) == 1
    assert Registry._global_schedules[key][0].cron == "0 2 * * *"


def test_multiple_schedules_all_registered_globally() -> None:
    reg = Registry("sched_test_11")

    @reg.operation
    def multi() -> None: ...

    multi.schedule("0 2 * * *", tz="UTC")
    multi.schedule("0 9 * * 1", tz="America/Chicago")

    key = "sched_test_11.multi"
    assert len(Registry._global_schedules[key]) == 2


def test_operation_remains_hashable_with_schedules() -> None:
    reg = Registry("sched_test_hash")

    @reg.operation
    def hashable_op() -> None: ...

    hashable_op.schedule("0 2 * * *", tz="UTC")
    hashable_op.schedule("0 9 * * 1", tz="America/New_York")

    # frozen dataclass carrying a mutable list must stay hashable (the list is
    # excluded from eq/hash) so Operations can be used as dict keys / set members.
    assert hash(hashable_op) is not None
    assert hashable_op in {hashable_op}


def test_validation_does_not_import_worker_or_server() -> None:
    import sys

    before_worker = "thrum.jobs.worker" in sys.modules
    before_server = "thrum.jobs.server" in sys.modules

    reg = Registry("sched_test_12")

    @reg.operation
    def check_imports() -> None: ...

    check_imports.schedule("0 2 * * *", tz="America/Vancouver")

    if not before_worker:
        assert "thrum.jobs.worker" not in sys.modules
    if not before_server:
        assert "thrum.jobs.server" not in sys.modules
