"""Offline tests for the read-only SessionDB history replay tool."""

from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from hermes_state import SessionDB
from hermes_state_replay import (
    REPLAY_MAX_TEXT_CHARS,
    ReplayInputError,
    _rollback_evidence,
    replay_database,
    run_replay,
    write_replay_report,
)


def _make_copy(path):
    db = SessionDB(path)
    db.create_session("replay-session", source="onebot", model="test-model")
    db.append_message(
        "replay-session",
        "user",
        [{"type": "text", "text": "memory replay marker"}],
    )
    db.append_message("replay-session", "assistant", "replay response")
    db.close()
    return path


def test_replay_is_read_only_and_reports_v11_plan(tmp_path):
    source = _make_copy(tmp_path / "copied-state.db")
    before = hashlib.sha256(source.read_bytes()).hexdigest()

    report = replay_database(source, queries=["replay marker", "memory"])

    assert report["read_only"] is True
    assert report["write_gate_open"] is False
    assert report["source"]["sha256"] == before
    assert report["rollback_evidence"]["source_unchanged"] is True
    assert report["rollback_evidence"]["write_operations"] == 0
    assert report["v26_probe"]["state"] == "legacy_v11"
    assert report["migration_plan"]["write_gate_open"] is False
    assert report["import_dry_run"]["status"] == "dry_run"
    assert report["import_dry_run"]["would_import_count"] == 1
    assert all("content" not in item for item in report["search"])
    assert report["search"][0]["match_count"] >= 1


def test_replay_does_not_emit_session_or_message_bodies(tmp_path):
    source = _make_copy(tmp_path / "private-copy.db")

    report = replay_database(source)
    encoded = json.dumps(report, ensure_ascii=False)

    assert "memory replay marker" not in encoded
    assert "replay response" not in encoded
    assert report["export_audit"]["ok"] is True


def test_replay_refuses_current_runtime_database(tmp_path, monkeypatch):
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()
    runtime_db = _make_copy(runtime_home / "state.db")
    monkeypatch.setenv("HERMES_HOME", str(runtime_home))

    with pytest.raises(ReplayInputError, match="current runtime state.db"):
        replay_database(runtime_db)


def test_replay_refuses_symlinks_and_does_not_create_missing_source(tmp_path):
    source = tmp_path / "missing.db"
    with pytest.raises(ReplayInputError):
        replay_database(source)
    assert not source.exists()

    real = _make_copy(tmp_path / "real.db")
    link = tmp_path / "link.db"
    try:
        link.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    with pytest.raises(ReplayInputError, match="symlink"):
        replay_database(link)


def test_report_writer_is_atomic_and_bounded(tmp_path):
    source = _make_copy(tmp_path / "source.db")
    report = replay_database(source)
    output = tmp_path / "reports" / "replay.json"

    written = write_replay_report(report, output)

    assert written == output.resolve()
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded["read_only"] is True
    assert not list(output.parent.glob(".replay.json.tmp-*"))


def test_report_writer_refuses_to_overwrite_source_copy(tmp_path):
    source = _make_copy(tmp_path / "same-name.db")
    report = replay_database(source)

    with pytest.raises(ReplayInputError, match="overwrite"):
        write_replay_report(report, source)

    assert source.is_file()


def test_cli_entrypoint_runs_from_module_and_writes_report(tmp_path):
    from scripts.sessiondb_replay import main

    source = _make_copy(tmp_path / "cli-source.db")
    output = tmp_path / "reports" / "replay.json"

    assert main(["--source", str(source), "--query", "memory", "--output", str(output)]) == 0
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded["read_only"] is True
    assert loaded["rollback_evidence"]["source_unchanged"] is True


def test_replay_handles_minimal_v26_shape_without_writes(tmp_path):
    source = tmp_path / "v26-copy.db"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    connection.execute("INSERT INTO schema_version(version) VALUES (26)")
    connection.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, started_at REAL)")
    connection.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp REAL)")
    connection.execute("CREATE TABLE state_meta (key TEXT PRIMARY KEY, value TEXT)")
    connection.commit()
    connection.close()

    report = replay_database(source)

    assert report["v26_probe"]["schema_version"] == 26
    assert report["v26_probe"]["state"] == "partial_v26"
    assert report["migration_plan"]["source_schema_version"] == 26
    assert report["rollback_evidence"]["source_unchanged"] is True


def test_canonical_report_exposes_legacy_aliases_and_cli_shape(tmp_path):
    source = _make_copy(tmp_path / "canonical.db")

    report = run_replay(source, search_terms=("memory",))
    payload = report.to_dict()

    assert report.source["sha256"] == report.source_sha256
    assert report.source["name"].startswith("source-copy-")
    assert "canonical.db" not in json.dumps(report.to_dict(), ensure_ascii=False)
    assert report.search[0]["match_count"] >= 1
    assert report.rollback_evidence["source_unchanged"] is True
    assert payload["source"]["sha256"] == report.source_sha256
    assert payload["search"][0]["match_count"] >= 1
    assert payload["export_capture"] == payload["export"]


def test_replay_bounds_v26_text_in_export_and_search(tmp_path):
    source = tmp_path / "bounded-v26.db"
    connection = sqlite3.connect(source)
    connection.executescript(
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version(version) VALUES (26);
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT,
            started_at REAL,
            system_prompt TEXT,
            profile_name TEXT
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            session_id TEXT,
            role TEXT,
            content TEXT,
            timestamp REAL,
            api_content TEXT,
            display_metadata TEXT
        );
        CREATE TABLE state_meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    prefix = "visible-prefix"
    hidden = "secret-after-boundary"
    giant = prefix + ("x" * (REPLAY_MAX_TEXT_CHARS + 100)) + hidden
    connection.execute(
        "INSERT INTO sessions(id, source, started_at, system_prompt, profile_name) VALUES (?, ?, ?, ?, ?)",
        ("session-secret-id", "onebot", 1.0, giant, "profile-a"),
    )
    connection.execute(
        "INSERT INTO messages(id, session_id, role, content, timestamp, api_content, display_metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, "session-secret-id", "user", giant, 2.0, giant, giant),
    )
    connection.commit()
    connection.close()

    report = run_replay(source, search_terms=(hidden, prefix))
    encoded = json.dumps(report.to_dict(), ensure_ascii=False)

    assert hidden not in encoded
    assert report.search_summary["terms"][0]["match_count"] == 0
    assert report.search_summary["terms"][1]["match_count"] == 1
    assert "profile_name" in report.export_audit["unknown_session_fields"]
    assert "api_content" in report.export_audit["unknown_message_fields"]


def test_replay_rejects_sqlite_sidecar_symlink(tmp_path):
    source = _make_copy(tmp_path / "sidecar-source.db")
    target = tmp_path / "outside-wal"
    target.write_bytes(b"not a wal")
    sidecar = tmp_path / "sidecar-source.db-wal"
    try:
        sidecar.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")

    with pytest.raises(ReplayInputError, match="sidecar symlink"):
        run_replay(source)


def test_shm_read_lock_tolerance_requires_stable_wal_and_regular_shape():
    base = {
        "main": {"present": True, "size": 10, "sha256": "main"},
        "wal": {"present": True, "size": 20, "sha256": "wal"},
        "journal": {"present": False, "size": 0, "sha256": ""},
        "shm": {"present": True, "size": 32_768, "sha256": "before"},
    }
    changed = {
        **base,
        "shm": {"present": True, "size": 32_768, "sha256": "after"},
    }

    tolerated = _rollback_evidence(
        base,
        changed,
        tolerate_wal_shm_read_locks=True,
        journal_mode="wal",
    )
    assert tolerated["source_unchanged"] is True
    assert tolerated["shm_read_lock_change_tolerated"] is True

    malformed = {
        **changed,
        "shm": {
            "present": True,
            "size": 12,
            "sha256": "after",
            "error": "non-regular sidecar is not read",
        },
    }
    rejected = _rollback_evidence(
        base,
        malformed,
        tolerate_wal_shm_read_locks=True,
        journal_mode="wal",
    )
    assert rejected["source_unchanged"] is False
    assert rejected["shm_read_lock_change_tolerated"] is False

    non_wal = _rollback_evidence(
        base,
        changed,
        tolerate_wal_shm_read_locks=True,
        journal_mode="delete",
    )
    assert non_wal["source_unchanged"] is False
