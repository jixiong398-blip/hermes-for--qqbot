"""Offline contracts for the optional durable gateway routing index."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import shutil

import pytest

import hermes_state
import hermes_state_v26_compat as v26
from gateway.config import GatewayConfig, Platform
from gateway.session import SessionEntry, SessionSource, SessionStore


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_v11(path):
    database = hermes_state.SessionDB(path)
    database.create_session("routing-session", source="onebot")
    database.close()
    return path


def _make_v26_routing_copy(path, tmp_path):
    target = _make_v11(path)
    backup = tmp_path / "routing-backup.db"
    shutil.copyfile(target, backup)
    report = v26.apply_v26_copy_gate(
        target,
        enable=True,
        dry_run=False,
        backup_path=backup,
        expected_sha256=_digest(target),
        tables=("gateway_routing",),
    )
    assert report.ok is True
    return target


def _entry(session_key="route:key", session_id="routing-session"):
    now = datetime(2026, 8, 31, 12, 0, 0)
    source = SessionSource(
        platform=Platform.LOCAL,
        chat_id="chat-id",
        chat_type="dm",
    )
    return SessionEntry(
        session_key=session_key,
        session_id=session_id,
        created_at=now,
        updated_at=now,
        origin=source,
        platform=Platform.LOCAL,
    )


def _install_db_factory(monkeypatch, path):
    real_session_db = hermes_state.SessionDB
    monkeypatch.setattr(
        hermes_state,
        "SessionDB",
        lambda: real_session_db(db_path=path),
    )


def test_v11_routing_crud_is_noop_without_ddl(tmp_path):
    target = _make_v11(tmp_path / "v11.db")
    before = _digest(target)
    database = hermes_state.SessionDB(target)
    try:
        payload = json.dumps(_entry().to_dict())
        assert database.gateway_routing_available() is False
        assert database.save_gateway_routing_entry("key", payload) is False
        assert database.replace_gateway_routing_entries({"key": payload}) is False
        assert database.load_gateway_routing_entries() == {}
        assert database.delete_gateway_routing_entries(["key"]) == 0
        assert database.delete_gateway_routing_entries_for_sessions(["routing-session"]) == 0
    finally:
        database.close()
    assert _digest(target) == before


def test_routing_crud_fails_closed_for_malformed_table(tmp_path):
    target = _make_v11(tmp_path / "malformed.db")
    connection = hermes_state.sqlite3.connect(target)
    try:
        connection.execute(
            "CREATE TABLE gateway_routing (scope TEXT, session_key TEXT)"
        )
        connection.commit()
    finally:
        connection.close()

    database = hermes_state.SessionDB(target)
    try:
        payload = json.dumps(_entry().to_dict())
        assert database.gateway_routing_available() is False
        assert database.save_gateway_routing_entry("key", payload) is False
        assert database.replace_gateway_routing_entries({"key": payload}) is False
        assert database.load_gateway_routing_entries() == {}
        assert database.delete_gateway_routing_entries(["key"]) == 0
    finally:
        database.close()


def test_routing_crud_rejects_complete_columns_without_upstream_constraints(tmp_path):
    target = _make_v11(tmp_path / "malformed-constraints.db")
    connection = hermes_state.sqlite3.connect(target)
    try:
        connection.execute(
            "CREATE TABLE gateway_routing ("
            "scope TEXT, session_key TEXT, entry_json TEXT, updated_at REAL)"
        )
        connection.commit()
    finally:
        connection.close()

    database = hermes_state.SessionDB(target)
    try:
        payload = json.dumps(_entry().to_dict())
        assert database.gateway_routing_available() is False
        assert database.save_gateway_routing_entry("key", payload) is False
        assert database.replace_gateway_routing_entries({"key": payload}) is False
        assert database.load_gateway_routing_entries() == {}
    finally:
        database.close()


def test_v26_routing_crud_is_scope_isolated_and_idempotent(tmp_path):
    target = _make_v26_routing_copy(tmp_path / "v26.db", tmp_path)
    database = hermes_state.SessionDB(target)
    try:
        first = json.dumps(_entry("key-a", "session-a").to_dict())
        second = json.dumps(_entry("key-b", "session-b").to_dict())
        assert database.save_gateway_routing_entry(
            "key-a", first, scope="scope-a", updated_at=10.0
        ) is True
        assert database.save_gateway_routing_entry(
            "key-a", second, scope="scope-b", updated_at=11.0
        ) is True
        assert json.loads(
            database.load_gateway_routing_entries(scope="scope-a")["key-a"]
        )["session_id"] == "session-a"
        assert json.loads(
            database.load_gateway_routing_entries(scope="scope-b")["key-a"]
        )["session_id"] == "session-b"

        replacement = json.dumps(_entry("key-c", "session-c").to_dict())
        assert database.replace_gateway_routing_entries(
            {"key-c": replacement}, scope="scope-a", updated_at=12.0
        ) is True
        assert set(database.load_gateway_routing_entries(scope="scope-a")) == {"key-c"}
        assert set(database.load_gateway_routing_entries(scope="scope-b")) == {"key-a"}
        assert database.delete_gateway_routing_entries(["key-c"], scope="scope-a") == 1
        assert database.load_gateway_routing_entries(scope="scope-a") == {}

        assert database.save_gateway_routing_entry(
            "key-d",
            json.dumps(_entry("key-d", "session-d").to_dict()),
            scope="scope-a",
        ) is True
        assert database.delete_gateway_routing_entries_for_sessions(
            ["session-d"], scope="scope-a"
        ) == 1
        assert database.load_gateway_routing_entries(scope="scope-a") == {}
    finally:
        database.close()


def test_routing_crud_rejects_invalid_json_and_length_without_partial_replace(tmp_path):
    target = _make_v26_routing_copy(tmp_path / "invalid.db", tmp_path)
    database = hermes_state.SessionDB(target)
    try:
        valid = json.dumps(_entry().to_dict())
        assert database.save_gateway_routing_entry("valid", valid, scope="scope") is True
        assert database.save_gateway_routing_entry("bad", "[]", scope="scope") is False
        assert database.save_gateway_routing_entry("bad", "not-json", scope="scope") is False
        assert database.save_gateway_routing_entry(
            "k" * (hermes_state.MAX_GATEWAY_ROUTING_KEY_CHARS + 1),
            valid,
            scope="scope",
        ) is False
        assert database.save_gateway_routing_entry(
            "scope-key",
            valid,
            scope="s" * (hermes_state.MAX_GATEWAY_ROUTING_SCOPE_CHARS + 1),
        ) is False
        assert database.save_gateway_routing_entry(
            "large",
            "{" + "\"x\":\"" + ("x" * hermes_state.MAX_GATEWAY_ROUTING_ENTRY_BYTES) + "\"}",
            scope="scope",
        ) is False
        assert database.replace_gateway_routing_entries(
            {"valid": valid, "bad": "not-json"}, scope="scope"
        ) is False
        assert set(database.load_gateway_routing_entries(scope="scope")) == {"valid"}
    finally:
        database.close()


def test_gateway_config_durable_routing_roundtrips_with_false_default():
    default = GatewayConfig()
    assert default.durable_routing is False
    assert GatewayConfig.from_dict(default.to_dict()).durable_routing is False
    enabled = GatewayConfig(durable_routing=True)
    assert GatewayConfig.from_dict(enabled.to_dict()).durable_routing is True


def test_gateway_config_loader_bridges_top_level_durable_routing(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    (tmp_path / "config.yaml").write_text(
        "durable_routing: true\n",
        encoding="utf-8",
    )

    from gateway.config import load_gateway_config

    assert load_gateway_config().durable_routing is True


def test_session_store_default_remains_json_only(tmp_path, monkeypatch):
    db_path = _make_v11(tmp_path / "default.db")
    _install_db_factory(monkeypatch, db_path)
    sessions_dir = tmp_path / "sessions"
    config = GatewayConfig(sessions_dir=sessions_dir, durable_routing=False)
    store = SessionStore(sessions_dir, config)
    try:
        entry = _entry("default-key", "default-session")
        store._entries[entry.session_key] = entry
        store._save()
        assert (sessions_dir / "sessions.json").exists()
        assert store._db.gateway_routing_available() is False
    finally:
        store._db.close()


def test_durable_store_prefers_db_and_uses_path_free_scope(tmp_path, monkeypatch):
    db_path = _make_v26_routing_copy(tmp_path / "durable.db", tmp_path)
    _install_db_factory(monkeypatch, db_path)
    sessions_dir = tmp_path / "sessions"
    config = GatewayConfig(sessions_dir=sessions_dir, durable_routing=True)
    store = SessionStore(sessions_dir, config)
    try:
        db_entry = _entry("durable-key", "db-session")
        fallback_entry = _entry("durable-key", "json-session")
        scope = store._routing_scope()
        assert str(sessions_dir) not in scope
        assert len(scope) <= hermes_state.MAX_GATEWAY_ROUTING_SCOPE_CHARS
        assert store._db.save_gateway_routing_entry(
            "durable-key",
            json.dumps(db_entry.to_dict()),
            scope=scope,
        ) is True
        sessions_dir.mkdir(parents=True, exist_ok=True)
        (sessions_dir / "sessions.json").write_text(
            json.dumps({"durable-key": fallback_entry.to_dict()}),
            encoding="utf-8",
        )

        store._ensure_loaded()
        assert store._entries["durable-key"].session_id == "db-session"
    finally:
        store._db.close()


def test_durable_store_falls_back_to_sessions_json_on_db_failure(tmp_path, monkeypatch):
    db_path = _make_v11(tmp_path / "fallback.db")
    _install_db_factory(monkeypatch, db_path)
    sessions_dir = tmp_path / "sessions"
    config = GatewayConfig(sessions_dir=sessions_dir, durable_routing=True)
    store = SessionStore(sessions_dir, config)

    class BrokenDB:
        def gateway_routing_available(self):
            return True

        def load_gateway_routing_entries(self, **_kwargs):
            raise RuntimeError("private db path")

        def replace_gateway_routing_entries(self, *_args, **_kwargs):
            raise RuntimeError("private db path")

    store._db.close()
    store._db = BrokenDB()
    entry = _entry("fallback-key", "fallback-session")
    sessions_dir.mkdir(parents=True, exist_ok=True)
    sessions_file = sessions_dir / "sessions.json"
    sessions_file.write_text(
        json.dumps({entry.session_key: entry.to_dict()}),
        encoding="utf-8",
    )

    store._ensure_loaded()
    assert store._entries[entry.session_key].session_id == entry.session_id
    store._save()
    assert json.loads(sessions_file.read_text(encoding="utf-8"))[entry.session_key]["session_id"] == entry.session_id
