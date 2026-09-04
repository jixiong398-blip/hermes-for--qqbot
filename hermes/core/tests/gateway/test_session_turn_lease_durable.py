"""Offline contracts for the opt-in durable SessionDB turn lease."""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import sqlite3
import time

import pytest

import hermes_state
import hermes_state_v26_compat as v26
from gateway.config import GatewayConfig
from gateway.turn_lease import (
    DurableSessionTurnLease,
    SessionTurnLeasePersistence,
)


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_v11(path):
    database = hermes_state.SessionDB(path)
    database.create_session("lease-session", source="test")
    database.close()
    return path


def _make_v26_lease_copy(path, tmp_path):
    target = _make_v11(path)
    backup = tmp_path / "lease-backup.db"
    shutil.copyfile(target, backup)
    report = v26.apply_v26_copy_gate(
        target,
        enable=True,
        dry_run=False,
        backup_path=backup,
        expected_sha256=_digest(target),
        tables=("session_turn_leases",),
    )
    assert report.ok is True
    return target


def _run(coro):
    return asyncio.run(coro)


def test_v11_lease_methods_are_noops_without_ddl(tmp_path):
    target = _make_v11(tmp_path / "v11.db")
    before = _digest(target)
    database = hermes_state.SessionDB(target)
    try:
        assert database.session_turn_leases_available() is False
        assert database.try_acquire_session_turn_lease("conversation", "holder") is False
        assert database.refresh_session_turn_lease("conversation", "holder") is False
        assert database.release_session_turn_lease("conversation", "holder") is False
        assert database.get_session_turn_lease("conversation") is None
    finally:
        database.close()
    assert _digest(target) == before


def test_v26_lease_acquire_refresh_release_and_owner_fencing(tmp_path):
    target = _make_v26_lease_copy(tmp_path / "v26.db", tmp_path)
    database = hermes_state.SessionDB(target)
    try:
        assert database.session_turn_leases_available() is True
        assert database.try_acquire_session_turn_lease(
            "conversation", "holder-a", ttl_seconds=10.0, now=100.0
        ) is True
        current = database.get_session_turn_lease("conversation", now=100.0)
        assert current["holder"] == "holder-a"
        assert current["acquired_at"] == 100.0
        assert current["expires_at"] == 110.0

        assert database.try_acquire_session_turn_lease(
            "conversation", "holder-b", ttl_seconds=10.0, now=101.0
        ) is False
        assert database.refresh_session_turn_lease(
            "conversation", "holder-b", ttl_seconds=20.0, now=102.0
        ) is False
        assert database.release_session_turn_lease(
            "conversation", "holder-b"
        ) is False

        assert database.refresh_session_turn_lease(
            "conversation", "holder-a", ttl_seconds=20.0, now=103.0
        ) is True
        current = database.get_session_turn_lease("conversation", now=103.0)
        assert current["expires_at"] == 123.0
        assert database.release_session_turn_lease("conversation", "holder-a") is True
        assert database.release_session_turn_lease("conversation", "holder-a") is False
        assert database.get_session_turn_lease("conversation", now=104.0) is None
    finally:
        database.close()


def test_v26_expired_lease_is_reclaimed_and_same_holder_acquire_is_idempotent(tmp_path):
    target = _make_v26_lease_copy(tmp_path / "expiry.db", tmp_path)
    database = hermes_state.SessionDB(target)
    try:
        assert database.try_acquire_session_turn_lease(
            "conversation", "holder-a", ttl_seconds=5.0, now=100.0
        ) is True
        assert database.try_acquire_session_turn_lease(
            "conversation", "holder-a", ttl_seconds=50.0, now=102.0
        ) is True
        current = database.get_session_turn_lease("conversation", now=102.0)
        assert current["expires_at"] == 105.0

        assert database.try_acquire_session_turn_lease(
            "conversation", "holder-b", ttl_seconds=10.0, now=106.0
        ) is True
        current = database.get_session_turn_lease("conversation", now=106.0)
        assert current["holder"] == "holder-b"
        assert current["acquired_at"] == 106.0
        assert current["expires_at"] == 116.0
    finally:
        database.close()


def test_lease_inputs_are_bounded_and_unknown_rows_fail_closed(tmp_path):
    target = _make_v26_lease_copy(tmp_path / "invalid.db", tmp_path)
    database = hermes_state.SessionDB(target)
    try:
        long_id = "c" * (hermes_state.MAX_SESSION_TURN_LEASE_ID_CHARS + 1)
        long_holder = "h" * (hermes_state.MAX_SESSION_TURN_LEASE_HOLDER_CHARS + 1)
        assert database.try_acquire_session_turn_lease(long_id, "holder") is False
        assert database.try_acquire_session_turn_lease("conversation", long_holder) is False
        assert database.try_acquire_session_turn_lease("conversation", "holder", ttl_seconds=0) is False
        assert database.try_acquire_session_turn_lease(
            "conversation", "holder", ttl_seconds=float("inf")
        ) is False
        assert database.try_acquire_session_turn_lease(
            "conversation", "holder", now=float("nan")
        ) is False
        assert database.get_session_turn_lease(long_id) is None
    finally:
        database.close()


def test_lease_sqlite_busy_is_bounded_and_does_not_escape(tmp_path, monkeypatch):
    target = _make_v26_lease_copy(tmp_path / "busy.db", tmp_path)
    database = hermes_state.SessionDB(target)
    try:
        def fail(_callback):
            raise sqlite3.OperationalError("database is locked: private path")

        monkeypatch.setattr(database, "_execute_write", fail)
        assert database.try_acquire_session_turn_lease("conversation", "holder") is False
        assert database.refresh_session_turn_lease("conversation", "holder") is False
        assert database.release_session_turn_lease("conversation", "holder") is False
    finally:
        database.close()


def test_durable_async_lease_adapter_is_explicit_and_uses_worker_thread():
    class FakeDB:
        def __init__(self):
            self.calls = []

        def try_acquire_session_turn_lease(self, *args, **kwargs):
            self.calls.append(("acquire", args, kwargs))
            return True

        def refresh_session_turn_lease(self, *args, **kwargs):
            self.calls.append(("refresh", args, kwargs))
            return True

        def release_session_turn_lease(self, *args, **kwargs):
            self.calls.append(("release", args, kwargs))
            return True

        def get_session_turn_lease(self, *args, **kwargs):
            self.calls.append(("get", args, kwargs))
            return {"holder": "holder", "expires_at": 2.0}

    database = FakeDB()
    disabled = DurableSessionTurnLease(
        database, "conversation", "holder", enabled=False
    )
    assert _run(disabled.try_acquire()) is False
    assert _run(disabled.refresh()) is False
    assert _run(disabled.release()) is False
    assert _run(disabled.get()) is None
    assert database.calls == []

    enabled = DurableSessionTurnLease(
        database,
        "conversation",
        "holder",
        enabled=True,
        timeout_seconds=1.0,
    )
    assert _run(enabled.try_acquire(now=1.0)) is True
    assert _run(enabled.refresh(now=1.0)) is True
    assert _run(enabled.get(now=1.0))["holder"] == "holder"
    assert _run(enabled.release()) is True
    assert [call[0] for call in database.calls] == [
        "acquire", "refresh", "get", "release"
    ]


def test_durable_async_lease_timeout_and_exception_fail_closed():
    class SlowDB:
        def try_acquire_session_turn_lease(self, *_args, **_kwargs):
            time.sleep(0.2)
            return True

    slow = DurableSessionTurnLease(
        SlowDB(), "conversation", "holder", enabled=True, timeout_seconds=0.01
    )

    async def timeout_probe():
        task = asyncio.create_task(slow.try_acquire())
        await asyncio.sleep(0.03)
        task_finished = task.done()
        return task_finished, await task

    task_finished, timeout_result = _run(timeout_probe())
    assert task_finished is True
    assert timeout_result is False

    class BrokenDB:
        def __getattr__(self, _name):
            def fail(*_args, **_kwargs):
                raise RuntimeError("private db path")

            return fail

    broken = DurableSessionTurnLease(
        BrokenDB(), "conversation", "holder", enabled=True
    )
    assert _run(broken.try_acquire()) is False
    assert _run(broken.refresh()) is False
    assert _run(broken.release()) is False
    assert _run(broken.get()) is None

    factory = SessionTurnLeasePersistence(BrokenDB(), enabled=False)
    assert _run(factory.lease("conversation", "holder").try_acquire()) is False


def test_gateway_config_durable_turn_leases_roundtrips_and_loads(tmp_path, monkeypatch):
    default = GatewayConfig()
    assert default.durable_turn_leases is False
    assert GatewayConfig.from_dict(default.to_dict()).durable_turn_leases is False
    assert GatewayConfig.from_dict({"durable_turn_leases": True}).durable_turn_leases is True

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        "durable_turn_leases: true\n",
        encoding="utf-8",
    )
    from gateway.config import load_gateway_config

    assert load_gateway_config().durable_turn_leases is True
