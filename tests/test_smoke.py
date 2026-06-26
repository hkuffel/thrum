"""Smoke tests that lock in the scaffold's structural invariants."""

from __future__ import annotations


def test_core_import_does_not_pull_in_fastapi() -> None:
    """The import-discipline law: importing the SDK core must not load the server
    stack. Guards the whole `[server]`-extra boundary."""
    import sys

    # Drop any prior import so this is a real check.
    for mod in list(sys.modules):
        if mod == "fastapi" or mod.startswith("fastapi."):
            del sys.modules[mod]

    import thrum.jobs  # noqa: F401
    from thrum.jobs import Registry, enqueue, task  # noqa: F401

    assert "fastapi" not in sys.modules


def test_task_registration_and_identity() -> None:
    from thrum.jobs import Registry

    billing = Registry("billing")

    @billing.task
    def send_receipts() -> None: ...

    assert send_receipts.key == "billing.send_receipts"


def test_namespace_name_collision_fails_fast() -> None:
    import pytest

    from thrum.jobs import Registry

    reg = Registry("dup")

    @reg.task
    def job() -> None: ...

    with pytest.raises(ValueError):

        @reg.task(name="job")
        def other() -> None: ...
