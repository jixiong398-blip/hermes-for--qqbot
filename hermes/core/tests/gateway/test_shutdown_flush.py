"""Focused tests for durable gateway shutdown spooling."""

from __future__ import annotations

import json
import asyncio
import os
from pathlib import Path
import stat
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource
from gateway.shutdown_flush import (
    flush_agent_history_to_file,
    flush_pending_to_file,
    recover_pending_to_db,
    serialise_pending_value,
)


@pytest.fixture()
def spool_dir(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "pending_messages"
    path.mkdir()
    monkeypatch.setattr("gateway.shutdown_flush._get_flush_dir", lambda: path)
    return path


def _payloads(spool_dir: Path) -> list[dict]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(spool_dir.glob("pending-*.json"))
    ]


def test_flush_pending_string_resolves_session_id(spool_dir):
    count = flush_pending_to_file(
        {"agent:main:onebot:group:123": "queued text"},
        session_id_resolver=lambda _key: "session-123",
    )

    assert count == 1
    payload = _payloads(spool_dir)[0]
    assert payload["session_key"] == "agent:main:onebot:group:123"
    assert payload["data"]["session_id"] == "session-123"
    assert payload["data"]["text"] == "queued text"


def test_message_event_preserves_source_and_media():
    event = MessageEvent(
        text="look at this",
        source=SessionSource(
            platform=Platform.LOCAL,
            chat_id="chat-1",
            user_id="user-1",
        ),
        message_id="msg-1",
        media_urls=["image.png"],
        media_types=["image/png"],
    )

    data = serialise_pending_value(event)

    assert data["text"] == "look at this"
    assert data["message_id"] == "msg-1"
    assert data["source"]["platform"] == "local"
    assert data["source"]["chat_id"] == "chat-1"
    assert data["media_urls"] == ["image.png"]


def test_recovery_inserts_and_deletes_resolved_payload(spool_dir):
    flush_pending_to_file(
        {"session-key": "lost message"},
        session_id_resolver=lambda _key: "session-id",
    )
    db = MagicMock()

    recovered = recover_pending_to_db(db)

    assert recovered == 1
    db.append_message.assert_called_once_with(
        session_id="session-id",
        role="user",
        content="lost message",
    )
    assert list(spool_dir.glob("pending-*.json")) == []


def test_recovery_keeps_unresolved_and_database_failures(spool_dir):
    flush_pending_to_file({"unresolved": "keep me"})
    flush_pending_to_file(
        {"resolved": "retry me"},
        session_id_resolver=lambda _key: "session-id",
    )
    db = MagicMock()
    db.append_message.side_effect = RuntimeError("database unavailable")

    assert recover_pending_to_db(db) == 0
    assert len(list(spool_dir.glob("pending-*.json"))) == 2


def test_recovery_keeps_payload_when_database_returns_false(spool_dir):
    flush_pending_to_file(
        {"resolved": "retry me"},
        session_id_resolver=lambda _key: "session-id",
    )
    db = MagicMock()
    db.append_message.return_value = False

    assert recover_pending_to_db(db) == 0
    assert len(list(spool_dir.glob("pending-*.json"))) == 1


def test_recovery_closes_owned_db_on_interrupt(spool_dir, monkeypatch):
    flush_pending_to_file(
        {"resolved": "interrupt"},
        session_id_resolver=lambda _key: "session-id",
    )

    class InterruptingDB:
        closed = False

        def append_message(self, **_kwargs):
            raise KeyboardInterrupt

        def close(self):
            self.closed = True

    db = InterruptingDB()
    monkeypatch.setattr("hermes_state.SessionDB", lambda: db)

    with pytest.raises(KeyboardInterrupt):
        recover_pending_to_db()

    assert db.closed is True
    assert list(spool_dir.glob("pending-*.json"))


def test_agent_history_snapshot_is_manual_only(spool_dir):
    assert flush_agent_history_to_file("session-id", []) == 0
    assert flush_agent_history_to_file(
        "session-id", [{"role": "user", "content": "unsaved"}]
    ) == 1

    payload = _payloads(spool_dir)[0]
    assert payload["reason"] == "shutdown-with-unpersisted-agent-history"
    assert payload["messages"][0]["content"] == "unsaved"

    db = MagicMock()
    assert recover_pending_to_db(db) == 0
    db.append_message.assert_not_called()
    assert list(spool_dir.glob("pending-*.json"))


def test_recovery_accepts_top_level_session_id(spool_dir):
    path = spool_dir / "pending-top-level.json"
    path.write_text(
        json.dumps(
            {
                "session_key": "session-key",
                "session_id": "session-id",
                "data": {"text": "top-level id"},
            }
        ),
        encoding="utf-8",
    )
    db = MagicMock()

    assert recover_pending_to_db(db) == 1
    db.append_message.assert_called_once_with(
        session_id="session-id",
        role="user",
        content="top-level id",
    )
    assert not path.exists()


def test_recovery_honors_time_budget_and_keeps_remaining(spool_dir, monkeypatch):
    flush_pending_to_file(
        {"first": "one", "second": "two"},
        session_id_resolver=lambda key: f"session-{key}",
    )
    db = MagicMock()
    ticks = iter((0.0, 0.0, 2.0))
    monkeypatch.setattr("gateway.shutdown_flush.time.monotonic", lambda: next(ticks))

    assert recover_pending_to_db(db, time_budget=1.0) == 1
    assert db.append_message.call_count == 1
    assert len(list(spool_dir.glob("pending-*.json"))) == 1


def test_startup_recovery_does_not_block_gateway_start(monkeypatch, tmp_path):
    import gateway.run as gateway_run
    from gateway.config import GatewayConfig

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    class CleanExitRunner:
        def __init__(self, config):
            self.config = config
            self.adapters = {}
            self.should_exit_cleanly = True
            self.exit_reason = None

        async def start(self):
            return True

    started = threading.Event()
    release = threading.Event()

    def slow_recovery(_db):
        started.set()
        release.wait(2.0)
        return 0

    monkeypatch.setattr(gateway_run, "GatewayRunner", CleanExitRunner)
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_SHUTDOWN_RECOVERY_TIMEOUT_SECS", 0.01)
    monkeypatch.setattr("gateway.status.get_running_pid", lambda: None)
    monkeypatch.setattr("tools.skills_sync.sync_skills", lambda quiet=True: None)
    monkeypatch.setattr("hermes_logging.setup_logging", lambda **_kwargs: None)
    monkeypatch.setattr(
        "gateway.shutdown_flush.recover_pending_to_db", slow_recovery
    )

    async def _run_startup():
        task = asyncio.create_task(
            gateway_run.start_gateway(
                config=GatewayConfig(), replace=False, verbosity=None
            )
        )
        result = await asyncio.wait_for(task, timeout=0.5)
        assert started.wait(0.2)
        release.set()
        return result

    started_at = time.perf_counter()
    result = asyncio.run(_run_startup())
    elapsed = time.perf_counter() - started_at

    assert result is True
    assert elapsed < 0.5


def test_spool_rejects_regular_file_as_directory(tmp_path, monkeypatch):
    path = tmp_path / "pending_messages"
    path.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr("gateway.shutdown_flush._get_flush_dir", lambda: path)

    assert flush_pending_to_file({"session-key": "keep me"}) == 0
    assert recover_pending_to_db(MagicMock()) == 0
    assert path.read_text(encoding="utf-8") == "not a directory"


def test_spool_rejects_symlink_directory(tmp_path, monkeypatch):
    target = tmp_path / "outside"
    target.mkdir()
    (target / "pending-external.json").write_text(
        json.dumps({"data": {"session_id": "external", "text": "do not read"}}),
        encoding="utf-8",
    )
    link = tmp_path / "pending_messages"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this Windows host")
    monkeypatch.setattr("gateway.shutdown_flush._get_flush_dir", lambda: link)

    assert flush_pending_to_file({"session-key": "must stay out"}) == 0
    db = MagicMock()
    assert recover_pending_to_db(db) == 0
    db.append_message.assert_not_called()
    assert (target / "pending-external.json").exists()


def test_recovery_skips_symlink_payload(spool_dir, tmp_path):
    target = tmp_path / "outside-payload.json"
    target.write_text(
        json.dumps(
            {
                "data": {"session_id": "session-id", "text": "outside"}
            }
        ),
        encoding="utf-8",
    )
    link = spool_dir / "pending-link.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable on this Windows host")

    db = MagicMock()
    assert recover_pending_to_db(db) == 0
    db.append_message.assert_not_called()
    assert link.exists()
    assert target.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission semantics only")
def test_spool_directory_and_payload_are_private(spool_dir):
    flush_pending_to_file({"session-key": "private"})

    assert stat.S_IMODE(spool_dir.stat().st_mode) == 0o700
    payload = next(spool_dir.glob("pending-*.json"))
    assert stat.S_IMODE(payload.stat().st_mode) == 0o600


def test_runner_finalizer_spools_only_unflushed_tail(monkeypatch):
    from gateway.run import GatewayRunner

    captured = []
    monkeypatch.setattr(
        "gateway.shutdown_flush.flush_agent_history_to_file",
        lambda session_id, history: captured.append((session_id, history)) or len(history),
    )
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._cleanup_agent_resources = MagicMock()
    agent = SimpleNamespace(
        session_id="session-id",
        _session_messages=[
            {"role": "user", "content": "persisted"},
            {"role": "assistant", "content": "unsaved"},
        ],
        _last_flushed_db_idx=1,
    )

    runner._finalize_shutdown_agents({"session-key": agent})

    assert captured == [
        ("session-id", [{"role": "assistant", "content": "unsaved"}])
    ]
    assert agent._last_flushed_db_idx == 2
    runner._cleanup_agent_resources.assert_called_once_with(agent)


def test_runner_finalizer_deduplicates_agent_and_tail(monkeypatch):
    from gateway.run import GatewayRunner

    captured = []
    monkeypatch.setattr(
        "gateway.shutdown_flush.flush_agent_history_to_file",
        lambda session_id, history: captured.append((session_id, history)) or len(history),
    )
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._cleanup_agent_resources = MagicMock()
    agent = SimpleNamespace(
        session_id="session-id",
        _session_messages=[
            {"role": "user", "content": "persisted"},
            {"role": "assistant", "content": "unsaved"},
        ],
        _last_flushed_db_idx=1,
    )

    runner._finalize_shutdown_agents({"first": agent, "duplicate": agent})
    runner._finalize_shutdown_agents({"again": agent})

    assert captured == [
        ("session-id", [{"role": "assistant", "content": "unsaved"}])
    ]
    # Finalization is idempotent for the durable tail; resource cleanup may
    # still be called by a defensive second finalization pass.
    assert runner._cleanup_agent_resources.call_count == 2


def test_runner_finalizer_keeps_cursor_when_spool_fails(monkeypatch):
    from gateway.run import GatewayRunner

    monkeypatch.setattr(
        "gateway.shutdown_flush.flush_agent_history_to_file",
        lambda _session_id, _history: 0,
    )
    runner = GatewayRunner.__new__(GatewayRunner)
    runner._cleanup_agent_resources = MagicMock()
    agent = SimpleNamespace(
        session_id="session-id",
        _session_messages=[{"role": "user", "content": "unsaved"}],
        _last_flushed_db_idx=0,
    )

    runner._finalize_shutdown_agents({"session-key": agent})

    assert agent._last_flushed_db_idx == 0
    runner._cleanup_agent_resources.assert_called_once_with(agent)


def test_stop_spools_pending_queues_before_clearing(monkeypatch, tmp_path):
    """Drive the real stop path without pytest-asyncio.

    Adapter ``cancel_background_tasks`` clears its queue, so the spool hook
    must observe both queues before that call and before the runner clears its
    own dictionary.
    """
    from gateway.config import GatewayConfig, Platform
    from gateway.run import GatewayRunner

    class Adapter:
        def __init__(self):
            self._pending_messages = {
                "adapter-session": SimpleNamespace(text="adapter text")
            }

        async def cancel_background_tasks(self):
            self._pending_messages.clear()

        async def disconnect(self):
            return None

    async def _stop_runner():
        runner = object.__new__(GatewayRunner)
        runner.config = GatewayConfig()
        runner._running = True
        runner._shutdown_event = asyncio.Event()
        runner._exit_reason = None
        runner._exit_code = None
        runner._running_agents = {}
        runner._running_agents_ts = {}
        runner._pending_messages = {"runner-session": "runner text"}
        runner._pending_approvals = {}
        runner._background_tasks = set()
        runner._busy_ack_ts = {}
        runner._draining = False
        runner._restart_requested = False
        runner._restart_detached = False
        runner._restart_via_service = False
        runner._restart_drain_timeout = 0
        runner._stop_task = None
        runner._failed_platforms = {}
        runner._session_model_overrides = {}
        runner._update_runtime_status = MagicMock()
        runner.session_store = SimpleNamespace(
            _entries={},
            _db=None,
            _ensure_loaded=lambda: None,
        )
        adapter = Adapter()
        runner.adapters = {Platform.TELEGRAM: adapter}

        captured = []

        def capture(pending, **kwargs):
            captured.append(dict(pending))
            return len(pending)

        monkeypatch.setattr("gateway.shutdown_flush.flush_pending_to_file", capture)
        monkeypatch.setattr("gateway.run._hermes_home", tmp_path)
        monkeypatch.setattr("gateway.status.remove_pid_file", lambda: None)
        monkeypatch.setattr(
            "tools.process_registry.process_registry.kill_all", lambda: 0
        )
        monkeypatch.setattr(
            "tools.terminal_tool.cleanup_all_environments", lambda: None
        )
        monkeypatch.setattr("tools.browser_tool.cleanup_all_browsers", lambda: None)

        await GatewayRunner.stop(runner)

        assert list(captured[0]) == ["adapter-session"]
        assert captured[-1] == {"runner-session": "runner text"}
        assert runner._pending_messages == {}
        assert adapter._pending_messages == {}

    asyncio.run(_stop_runner())
