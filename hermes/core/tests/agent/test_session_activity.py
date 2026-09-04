"""Offline tests for the opt-in session activity observation contract."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3

import pytest

import hermes_state_v26_compat as v26
from hermes_state import SessionDB
from run_agent import AIAgent
from agent.session_activity import (
    ACTIVITY_DESCRIPTION_MAX,
    ActivityProvenance,
    bound_activity_description,
    build_activity_snapshot,
    normalize_activity_provenance,
    reset_session_activity_persist_window,
)


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_v11(path):
    database = SessionDB(path)
    database.create_session("activity-session", source="test")
    database.close()
    return path


def _make_v26_activity_copy(path, tmp_path):
    target = _make_v11(path)
    backup = tmp_path / "activity-backup.db"
    shutil.copyfile(target, backup)
    before = _digest(target)
    report = v26.apply_v26_copy_gate(
        target,
        enable=True,
        dry_run=False,
        backup_path=backup,
        expected_sha256=before,
        tables=(),
        columns=(
            "sessions.last_activity_at",
            "sessions.last_activity_description",
            "sessions.last_activity_provenance",
        ),
    )
    assert report.ok is True
    return target


def test_activity_contract_bounds_description_and_unknown_provenance():
    description = bound_activity_description("  " + ("x" * 200) + "  ")

    assert len(description) == ACTIVITY_DESCRIPTION_MAX
    assert description.endswith("...")
    assert normalize_activity_provenance("future.value") is ActivityProvenance.UNKNOWN
    assert normalize_activity_provenance(object()) is ActivityProvenance.UNKNOWN


def test_activity_snapshot_is_bounded_and_never_reports_negative_idle_time():
    snapshot = build_activity_snapshot(
        last_activity_at=200,
        last_activity_description="  activity  ",
        last_activity_provenance="agent.compression",
        now=100,
        extra={"private": "x" * 1000, "count": 3},
    )

    assert snapshot["last_activity_at"] == 200.0
    assert snapshot["last_activity_description"] == "activity"
    assert snapshot["last_activity_provenance"] == "agent.compression"
    assert snapshot["seconds_since_activity"] == 0.0
    assert len(snapshot["private"]) == 240
    assert snapshot["count"] == 3


def test_reset_helper_clears_only_agent_persist_window():
    class Agent:
        _session_activity_last_persist_mono = 42.0
        other = "preserve"

    agent = Agent()
    reset_session_activity_persist_window(agent)

    assert agent._session_activity_last_persist_mono == 0.0
    assert agent.other == "preserve"


def test_v11_session_activity_methods_are_noops_without_ddl(tmp_path):
    target = _make_v11(tmp_path / "v11.db")
    database = SessionDB(target)
    try:
        before = _digest(target)
        assert database.touch_session_activity(
            "activity-session",
            "working",
            provenance="agent.compression",
            now=100.0,
        ) is None
        assert database.get_session_activity("activity-session") is None
        assert database.clear_session_activity_labels("activity-session") is None
        assert _digest(target) == before
        assert database.get_session("activity-session").get("last_activity_at") is None
    finally:
        database.close()


def test_v26_activity_updates_monotonically_and_clear_preserves_timestamp(tmp_path):
    target = _make_v26_activity_copy(tmp_path / "v26-activity.db", tmp_path)
    database = SessionDB(target)
    try:
        database.touch_session_activity(
            "activity-session",
            "first activity",
            provenance="agent.compression",
            now=100.0,
        )
        first = database.get_session_activity("activity-session")
        assert first["last_activity_at"] == 100.0
        assert first["last_activity_description"] == "first activity"
        assert first["last_activity_provenance"] == "agent.compression"

        # A wall-clock regression cannot overwrite the durable observation.
        database.touch_session_activity(
            "activity-session",
            "older activity",
            provenance="agent.compression_timeout",
            now=90.0,
        )
        regressed = database.get_session_activity("activity-session")
        assert regressed["last_activity_at"] == 100.0
        assert regressed["last_activity_description"] == "first activity"

        database.touch_session_activity(
            "activity-session",
            "later activity",
            provenance="future-provenance",
            now=110.0,
        )
        later = database.get_session_activity("activity-session")
        assert later["last_activity_at"] == 110.0
        assert later["last_activity_description"] == "later activity"
        assert later["last_activity_provenance"] == "unknown"

        database.clear_session_activity_labels("activity-session")
        cleared = database.get_session_activity("activity-session")
        assert cleared["last_activity_at"] == 110.0
        assert cleared["last_activity_description"] == ""
        assert cleared["last_activity_provenance"] == "unknown"
    finally:
        database.close()


def test_v26_activity_force_persist_can_refresh_labels_at_same_timestamp(tmp_path):
    target = _make_v26_activity_copy(tmp_path / "v26-force.db", tmp_path)
    database = SessionDB(target)
    try:
        database.touch_session_activity("activity-session", "old", now=100.0)
        database.touch_session_activity(
            "activity-session",
            "new label",
            provenance=ActivityProvenance.AGENT_COMPRESSION_COOLDOWN,
            now=100.0,
            force_persist=True,
        )
        result = database.get_session_activity("activity-session")
        assert result["last_activity_at"] == 100.0
        assert result["last_activity_description"] == "new label"
        assert result["last_activity_provenance"] == "agent.compression_cooldown"
    finally:
        database.close()


def test_activity_sqlite_failure_is_bounded_and_does_not_escape(tmp_path, monkeypatch):
    target = _make_v26_activity_copy(tmp_path / "v26-failure.db", tmp_path)
    database = SessionDB(target)
    try:
        def fail(_callback):
            raise sqlite3.OperationalError("database is locked: private path")

        monkeypatch.setattr(database, "_execute_write", fail)
        database.touch_session_activity("activity-session", "working", now=100.0)
        database.clear_session_activity_labels("activity-session")
    finally:
        database.close()


def test_aiagent_activity_hook_is_opt_in_and_keeps_memory_summary(tmp_path):
    calls = []
    agent = object.__new__(AIAgent)
    agent.session_id = "activity-session"
    agent._last_activity_ts = 0.0
    agent._last_activity_desc = "initializing"
    agent._persist_session_activity = False
    agent._session_activity_callback = lambda *args, **kwargs: calls.append((args, kwargs))
    agent._session_activity_last_persist_mono = 0.0

    agent._touch_activity("x" * (ACTIVITY_DESCRIPTION_MAX + 20))

    assert len(agent._last_activity_desc) == ACTIVITY_DESCRIPTION_MAX
    assert calls
    assert calls[0][0] == ("activity-session", agent._last_activity_desc)
    assert calls[0][1]["provenance"] == "unknown"


def test_aiagent_session_db_persistence_requires_explicit_flag():
    calls = []

    class FakeDB:
        def touch_session_activity(self, *args, **kwargs):
            calls.append((args, kwargs))

    agent = object.__new__(AIAgent)
    agent.session_id = "activity-session"
    agent._last_activity_ts = 0.0
    agent._last_activity_desc = "initializing"
    agent._session_db = FakeDB()
    agent._persist_session_activity = False
    agent._session_activity_callback = None
    agent._session_activity_last_persist_mono = 0.0
    agent._touch_activity("no durable call")
    assert calls == []

    agent._persist_session_activity = True
    agent._touch_activity("durable call", force_persist=True)
    assert len(calls) == 1
    assert calls[0][0] == ("activity-session", "durable call")
