"""Offline tests for the explicit, copy-only v26 schema write gate."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading

import pytest

import hermes_state_v26_compat as compat
from hermes_state import SessionDB


def _make_v11(path):
    database = SessionDB(path)
    database.create_session("copy-session", source="test", model="copy-model")
    database.close()
    return path


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _backup(path, tmp_path):
    backup = tmp_path / "backup-copy.db"
    shutil.copyfile(path, backup)
    return backup


def test_default_gate_is_disabled_and_does_not_write(tmp_path):
    target = _make_v11(tmp_path / "target.db")
    before = _digest(target)

    report = compat.apply_v26_copy_gate(target)

    assert report.status == "disabled"
    assert report.ok is False
    assert report.write_operations == 0
    assert report.tables_would_create == ("system_prompts",)
    assert _digest(target) == before
    assert not list(tmp_path.glob("*.v26-gate.lock"))


def test_dry_run_reports_pending_tables_without_backup_or_write(tmp_path):
    target = _make_v11(tmp_path / "dry-run.db")
    before = _digest(target)

    report = SessionDB.apply_v26_copy_gate(
        target,
        enable=True,
        dry_run=True,
        tables=("system_prompts", "session_model_usage"),
    )

    assert report.status == "dry_run"
    assert report.ok is True
    assert report.tables_would_create == (
        "system_prompts",
        "session_model_usage",
    )
    assert report.tables_created == ()
    assert report.write_operations == 0
    assert report.backup_checked is False
    assert _digest(target) == before


def test_enabled_write_requires_expected_hash_and_distinct_backup(tmp_path):
    target = _make_v11(tmp_path / "requires-preconditions.db")

    report = compat.apply_v26_copy_gate(target, enable=True, dry_run=False)

    assert report.status == "rejected"
    assert report.errors == ("expected_hash_required",)
    assert report.write_operations == 0


def test_runtime_state_is_rejected_before_any_write(tmp_path, monkeypatch):
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()
    runtime_db = _make_v11(runtime_home / "state.db")
    monkeypatch.setenv("HERMES_HOME", str(runtime_home))

    report = compat.apply_v26_copy_gate(runtime_db, enable=True, dry_run=False)

    assert report.status == "rejected"
    assert report.accepted is False
    assert report.errors == ("runtime_state_rejected",)


def test_copy_gate_commits_additive_tables_and_preserves_backup(tmp_path):
    target = _make_v11(tmp_path / "account-bearing-name.db")
    backup = _backup(target, tmp_path)
    before = _digest(target)
    backup_before = _digest(backup)

    report = compat.apply_v26_copy_gate(
        target,
        enable=True,
        dry_run=False,
        backup_path=backup,
        expected_sha256=before,
        tables=("system_prompts", "session_model_usage"),
    )

    assert report.status == "committed"
    assert report.ok is True
    assert report.schema_version_before == 11
    assert report.schema_version_after == 11
    assert report.tables_created == (
        "system_prompts",
        "session_model_usage",
    )
    assert report.write_operations == 2
    assert report.backup_checked is True
    assert report.source_preserved is True
    assert report.backup_sha256_before == backup_before
    assert report.backup_sha256_after == backup_before
    assert report.backup_sidecars_unchanged is True
    assert _digest(backup) == backup_before

    connection = sqlite3.connect(target)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"system_prompts", "session_model_usage"} <= tables
        assert connection.execute(
            "SELECT version FROM schema_version"
        ).fetchone()[0] == 11
    finally:
        connection.close()

    encoded = json.dumps(report.to_dict(), ensure_ascii=False)
    assert str(target) not in encoded
    assert "account-bearing-name" not in encoded
    assert "state.db" not in encoded


def test_copy_gate_is_idempotent_without_requiring_old_backup_again(tmp_path):
    target = _make_v11(tmp_path / "idempotent.db")
    backup = _backup(target, tmp_path)
    first = compat.apply_v26_copy_gate(
        target,
        enable=True,
        dry_run=False,
        backup_path=backup,
        expected_sha256=_digest(target),
    )

    second = compat.apply_v26_copy_gate(
        target,
        enable=True,
        dry_run=False,
        expected_sha256=first.target_sha256_after,
    )

    assert first.status == "committed"
    assert second.status == "already_applied"
    assert second.ok is True
    assert second.write_operations == 0
    assert second.target_unchanged is True


def test_copy_gate_rejects_backup_hash_mismatch_without_writing(tmp_path):
    target = _make_v11(tmp_path / "target-hash.db")
    backup = tmp_path / "wrong-backup.db"
    backup.write_bytes(b"not the target copy")
    before = _digest(target)

    report = compat.apply_v26_copy_gate(
        target,
        enable=True,
        dry_run=False,
        backup_path=backup,
        expected_sha256=before,
    )

    assert report.status == "rejected"
    assert report.errors == ("backup_hash_mismatch",)
    assert report.write_operations == 0
    assert _digest(target) == before


def test_copy_gate_rejects_backup_sidecar_symlink(tmp_path):
    target = _make_v11(tmp_path / "target-sidecar.db")
    backup = _backup(target, tmp_path)
    outside = tmp_path / "outside-journal"
    outside.write_bytes(b"not a journal")
    sidecar = tmp_path / "backup-copy.db-journal"
    try:
        sidecar.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")

    report = compat.apply_v26_copy_gate(
        target,
        enable=True,
        dry_run=False,
        backup_path=backup,
        expected_sha256=_digest(target),
    )

    assert report.status == "rejected"
    assert report.errors == ("sidecar_symlink_rejected",)


def test_copy_gate_rolls_back_all_incremental_tables_on_failure(tmp_path, monkeypatch):
    target = _make_v11(tmp_path / "rollback.db")
    backup = _backup(target, tmp_path)
    before = _digest(target)
    original = compat._create_incremental_table

    def fail_on_second_table(connection, table):
        original(connection, table)
        if table == "session_model_usage":
            raise RuntimeError("synthetic failure with private detail")

    monkeypatch.setattr(compat, "_create_incremental_table", fail_on_second_table)
    report = compat.apply_v26_copy_gate(
        target,
        enable=True,
        dry_run=False,
        backup_path=backup,
        expected_sha256=before,
        tables=("system_prompts", "session_model_usage"),
    )

    assert report.status == "rolled_back"
    assert report.ok is False
    assert report.errors == ("RuntimeError",)
    assert report.tables_created == ()
    assert report.backup_unchanged is True
    assert "synthetic failure" not in json.dumps(report.to_dict())

    connection = sqlite3.connect(target)
    try:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='system_prompts'"
        ).fetchone() is None
    finally:
        connection.close()


def test_copy_gate_reports_sqlite_busy_with_bounded_timeout(tmp_path):
    target = _make_v11(tmp_path / "busy.db")
    backup = _backup(target, tmp_path)
    before = _digest(target)
    locker = sqlite3.connect(target, isolation_level=None, timeout=0)
    try:
        locker.execute("BEGIN IMMEDIATE")
        report = compat.apply_v26_copy_gate(
            target,
            enable=True,
            dry_run=False,
            backup_path=backup,
            expected_sha256=before,
            busy_timeout_ms=0,
        )
    finally:
        locker.rollback()
        locker.close()

    assert report.status == "busy"
    assert report.errors == ("sqlite_busy",)
    assert report.write_operations == 0


def test_copy_gate_file_lock_blocks_second_owner_and_has_no_path_in_report(tmp_path):
    target = _make_v11(tmp_path / "sensitive-owner-name.db")
    backup = _backup(target, tmp_path)
    before = _digest(target)
    first = compat._CopyGateFileLock(target)
    assert first.acquire(0) is True
    try:
        result = {}

        def attempt_second_owner():
            second = compat._CopyGateFileLock(target)
            result["acquired"] = second.acquire(25)
            second.release()

        worker = threading.Thread(target=attempt_second_owner)
        worker.start()
        worker.join(timeout=1.0)
        assert result == {"acquired": False}
        report = compat.apply_v26_copy_gate(
            target,
            enable=True,
            dry_run=False,
            backup_path=backup,
            expected_sha256=before,
            busy_timeout_ms=0,
        )
        assert report.status == "busy"
        assert report.errors == ("file_lock_busy",)
    finally:
        first.release()

    report = compat.apply_v26_copy_gate(target)
    encoded = json.dumps(report.to_dict(), ensure_ascii=False)
    assert str(tmp_path) not in encoded
    assert "sensitive-owner-name" not in encoded


def test_copy_gate_adds_explicit_column_batch_and_skips_existing_columns(tmp_path):
    target = _make_v11(tmp_path / "column-batch.db")
    connection = sqlite3.connect(target)
    try:
        connection.execute('ALTER TABLE sessions ADD COLUMN "session_key" TEXT')
        connection.commit()
    finally:
        connection.close()

    backup = _backup(target, tmp_path)
    before = _digest(target)

    report = compat.apply_v26_copy_gate(
        target,
        enable=True,
        dry_run=False,
        backup_path=backup,
        expected_sha256=before,
        columns=(
            "sessions.session_key",
            "sessions.chat_id",
            "messages.effect_disposition",
        ),
    )

    assert report.status == "committed"
    assert report.tables_created == ()
    assert report.columns_would_add == (
        "sessions.chat_id",
        "messages.effect_disposition",
    )
    assert report.columns_added == report.columns_would_add
    assert report.schema_version_after == 11
    assert report.source_preserved is True

    connection = sqlite3.connect(target)
    try:
        session_columns = {
            row[1] for row in connection.execute('PRAGMA table_info("sessions")')
        }
        message_columns = {
            row[1] for row in connection.execute('PRAGMA table_info("messages")')
        }
        assert {"session_key", "chat_id"} <= session_columns
        assert "effect_disposition" in message_columns
    finally:
        connection.close()


def test_copy_gate_sidecar_columns_are_explicit_and_schema_version_stays_v11(tmp_path):
    target = _make_v11(tmp_path / "sidecar-dry-run.db")
    before = _digest(target)
    sidecar_columns = (
        "messages.api_content",
        "messages.display_kind",
        "messages.display_metadata",
    )

    report = SessionDB.apply_v26_copy_gate(
        target,
        enable=True,
        dry_run=True,
        columns=sidecar_columns,
    )

    assert report.status == "dry_run"
    assert report.ok is True
    assert report.columns_would_add == sidecar_columns
    assert report.write_operations == 0
    assert _digest(target) == before

    connection = sqlite3.connect(target)
    try:
        assert connection.execute(
            "SELECT version FROM schema_version"
        ).fetchone()[0] == 11
        columns = {
            row[1]
            for row in connection.execute('PRAGMA table_info("messages")')
        }
        assert not (set(sidecar_columns) & {f"messages.{name}" for name in columns})
    finally:
        connection.close()


def test_copy_gate_sidecar_columns_commit_only_on_disposable_copy(tmp_path):
    target = _make_v11(tmp_path / "sidecar-copy.db")
    backup = _backup(target, tmp_path)
    before = _digest(target)

    report = SessionDB.apply_v26_copy_gate(
        target,
        enable=True,
        dry_run=False,
        backup_path=backup,
        expected_sha256=before,
        columns=(
            "messages.api_content",
            "messages.display_kind",
            "messages.display_metadata",
        ),
    )

    assert report.status == "committed"
    assert report.ok is True
    assert report.schema_version_before == report.schema_version_after == 11
    assert report.columns_added == (
        "messages.api_content",
        "messages.display_kind",
        "messages.display_metadata",
    )
    connection = sqlite3.connect(target)
    try:
        columns = {
            row[1]
            for row in connection.execute('PRAGMA table_info("messages")')
        }
        assert {"api_content", "display_kind", "display_metadata"} <= columns
    finally:
        connection.close()


def test_copy_gate_rejects_incompatible_existing_column(tmp_path):
    target = _make_v11(tmp_path / "incompatible-column.db")
    connection = sqlite3.connect(target)
    try:
        connection.execute('ALTER TABLE sessions ADD COLUMN "chat_id" INTEGER')
        connection.commit()
    finally:
        connection.close()
    backup = _backup(target, tmp_path)
    before = _digest(target)

    report = compat.apply_v26_copy_gate(
        target,
        enable=True,
        dry_run=False,
        backup_path=backup,
        expected_sha256=before,
        tables=(),
        columns=("sessions.chat_id",),
    )

    assert report.status == "rejected"
    assert report.errors == ("target_incremental_column_incompatible",)
    assert _digest(target) == before


def test_copy_gate_rolls_back_all_columns_on_second_column_failure(tmp_path, monkeypatch):
    target = _make_v11(tmp_path / "column-rollback.db")
    backup = _backup(target, tmp_path)
    before = _digest(target)
    original = compat._add_incremental_column

    def fail_on_second_column(connection, table, column, definition):
        original(connection, table, column, definition)
        if column == "chat_id":
            raise RuntimeError("synthetic column failure with private detail")

    monkeypatch.setattr(compat, "_add_incremental_column", fail_on_second_column)
    report = compat.apply_v26_copy_gate(
        target,
        enable=True,
        dry_run=False,
        backup_path=backup,
        expected_sha256=before,
        tables=(),
        columns=("sessions.session_key", "sessions.chat_id"),
    )

    assert report.status == "rolled_back"
    assert report.errors == ("RuntimeError",)
    assert report.columns_added == ()
    assert report.backup_unchanged is True
    assert "private detail" not in json.dumps(report.to_dict())

    connection = sqlite3.connect(target)
    try:
        columns = {
            row[1] for row in connection.execute('PRAGMA table_info("sessions")')
        }
        assert "session_key" not in columns
        assert "chat_id" not in columns
    finally:
        connection.close()


def test_copy_gate_rejects_unknown_column_before_target_write(tmp_path):
    target = _make_v11(tmp_path / "unknown-column.db")

    report = compat.apply_v26_copy_gate(
        target,
        enable=True,
        dry_run=True,
        tables=(),
        columns=("sessions.not_a_v26_column",),
    )

    assert report.status == "rejected"
    assert report.errors == ("column_not_allowed",)


def test_copy_gate_rejects_explicit_empty_column_batch(tmp_path):
    target = _make_v11(tmp_path / "empty-column-batch.db")

    report = compat.apply_v26_copy_gate(
        target,
        enable=True,
        dry_run=True,
        columns=(),
    )

    assert report.status == "rejected"
    assert report.errors == ("columns_invalid",)


_REMAINING_TABLE_SPECS = {
    "gateway_routing": {
        "scope": ("TEXT", True, "''"),
        "session_key": ("TEXT", True, None),
        "entry_json": ("TEXT", True, None),
        "updated_at": ("REAL", True, None),
    },
    "gateway_hygiene_state": {
        "session_key": ("TEXT", False, None),
        "failure_streak": ("INTEGER", True, "0"),
    },
    "compression_locks": {
        "session_id": ("TEXT", False, None),
        "holder": ("TEXT", True, None),
        "acquired_at": ("REAL", True, None),
        "expires_at": ("REAL", True, None),
    },
    "session_turn_leases": {
        "conversation_id": ("TEXT", False, None),
        "holder": ("TEXT", True, None),
        "acquired_at": ("REAL", True, None),
        "expires_at": ("REAL", True, None),
    },
    "async_delegations": {
        "delegation_id": ("TEXT", False, None),
        "origin_session": ("TEXT", True, None),
        "origin_ui_session_id": ("TEXT", True, "''"),
        "parent_session_id": ("TEXT", False, None),
        "state": ("TEXT", True, None),
        "dispatched_at": ("REAL", True, None),
        "completed_at": ("REAL", False, None),
        "updated_at": ("REAL", True, None),
        "event_json": ("TEXT", False, None),
        "result_json": ("TEXT", False, None),
        "delivery_state": ("TEXT", True, "'pending'"),
        "delivery_attempts": ("INTEGER", True, "0"),
        "delivered_at": ("REAL", False, None),
        "owner_pid": ("INTEGER", False, None),
        "owner_started_at": ("INTEGER", False, None),
        "task_json": ("TEXT", False, None),
        "delivery_claim": ("TEXT", False, None),
        "delivery_claimed_at": ("REAL", False, None),
    },
}


@pytest.mark.parametrize("table", tuple(_REMAINING_TABLE_SPECS))
def test_copy_gate_creates_remaining_v26_state_table_with_upstream_shape(tmp_path, table):
    target = _make_v11(tmp_path / f"{table}.db")
    backup = _backup(target, tmp_path)
    report = compat.apply_v26_copy_gate(
        target,
        enable=True,
        dry_run=False,
        backup_path=backup,
        expected_sha256=_digest(target),
        tables=(table,),
    )

    assert report.status == "committed"
    assert report.tables_created == (table,)
    connection = sqlite3.connect(target)
    try:
        rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        actual = {
            row[1]: (str(row[2]).upper(), bool(row[3]), row[4])
            for row in rows
        }
        expected = _REMAINING_TABLE_SPECS[table]
        assert set(actual) == set(expected)
        for column, (column_type, notnull, default) in expected.items():
            assert actual[column] == (column_type, notnull, default)
        primary_key = tuple(row[1] for row in rows if row[5])
        expected_primary_key = {
            "gateway_routing": ("scope", "session_key"),
            "gateway_hygiene_state": ("session_key",),
            "compression_locks": ("session_id",),
            "session_turn_leases": ("conversation_id",),
            "async_delegations": ("delegation_id",),
        }[table]
        assert primary_key == expected_primary_key
        assert connection.execute(
            "SELECT version FROM schema_version"
        ).fetchone()[0] == 11
    finally:
        connection.close()


def test_copy_gate_rejects_duplicate_table_selection(tmp_path):
    target = _make_v11(tmp_path / "duplicate-table.db")

    report = compat.apply_v26_copy_gate(
        target,
        enable=True,
        dry_run=True,
        tables=("gateway_routing", "gateway_routing"),
    )

    assert report.status == "rejected"
    assert report.errors == ("table_duplicate",)


def test_copy_gate_rejects_incompatible_remaining_table(tmp_path):
    target = _make_v11(tmp_path / "bad-routing.db")
    connection = sqlite3.connect(target)
    try:
        connection.execute(
            "CREATE TABLE gateway_routing (scope TEXT, session_key TEXT PRIMARY KEY)"
        )
        connection.commit()
    finally:
        connection.close()
    backup = _backup(target, tmp_path)

    report = compat.apply_v26_copy_gate(
        target,
        enable=True,
        dry_run=False,
        backup_path=backup,
        expected_sha256=_digest(target),
        tables=("gateway_routing",),
    )

    assert report.status == "rejected"
    assert report.errors == ("target_incremental_table_incompatible",)


def test_copy_gate_rolls_back_mixed_table_and_column_batch(tmp_path, monkeypatch):
    target = _make_v11(tmp_path / "mixed-rollback.db")
    backup = _backup(target, tmp_path)
    before = _digest(target)
    original = compat._add_incremental_column

    def fail_on_second_column(connection, table, column, definition):
        original(connection, table, column, definition)
        if column == "chat_id":
            raise RuntimeError("synthetic mixed batch failure")

    monkeypatch.setattr(compat, "_add_incremental_column", fail_on_second_column)
    report = compat.apply_v26_copy_gate(
        target,
        enable=True,
        dry_run=False,
        backup_path=backup,
        expected_sha256=before,
        tables=("gateway_routing",),
        columns=("sessions.session_key", "sessions.chat_id"),
    )

    assert report.status == "rolled_back"
    assert report.tables_created == ()
    assert report.columns_added == ()
    assert report.errors == ("RuntimeError",)

    connection = sqlite3.connect(target)
    try:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gateway_routing'"
        ).fetchone() is None
        columns = {
            row[1] for row in connection.execute('PRAGMA table_info("sessions")')
        }
        assert "session_key" not in columns
        assert "chat_id" not in columns
    finally:
        connection.close()
