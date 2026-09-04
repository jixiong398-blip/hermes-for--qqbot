"""Windows-first disposable replay/WAL harness contract tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.sessiondb_replay_harness import run_disposable_harness


def test_disposable_harness_proves_lineage_wal_and_read_only_replay():
    report = run_disposable_harness()

    assert report.ok is True
    assert report.status == "ok"
    assert report.fixture["schema_version"] == 11
    assert report.fixture["v26_runtime_tables"] == 0
    assert report.lineage == {
        "compression_parent_child": True,
        "late_event_order": True,
    }
    assert report.wal["writer_ready"] is True
    assert report.wal["writer_exit_code"] == 0
    assert report.wal["source_main_wal_unchanged_after_replay"] is True
    assert isinstance(report.wal["reader_shm_changed"], bool)
    assert report.replay["subprocess_ok"] is True
    assert report.replay["report_ok"] is True
    assert report.replay["read_only"] is True
    assert report.replay["write_gate_open"] is False
    assert report.replay["wal_journal_mode"] == "wal"
    assert report.replay["wal_probe_matches"] == 1
    assert report.replay["source_unchanged"] is True
    assert report.replay["shm_changed_during_read"] == report.wal["reader_shm_changed"]
    if report.replay["shm_changed_during_read"]:
        assert report.replay["shm_read_lock_change_tolerated"] is True
    assert report.cleanup == {
        "temporary_disposed": True,
        "source_removed": True,
        "report_removed": True,
    }
    assert report.errors == ()


def test_disposable_harness_report_is_bounded_and_path_free(tmp_path):
    report = run_disposable_harness()
    encoded = json.dumps(report.to_dict(), ensure_ascii=False)

    assert str(tmp_path) not in encoded
    assert "fixture-root" not in encoded
    assert "fixture-child" not in encoded
    assert len(encoded) < 10_000
    assert set(report.to_dict()) == {
        "ok",
        "status",
        "platform",
        "fixture",
        "lineage",
        "wal",
        "replay",
        "cleanup",
        "errors",
    }


def test_disposable_harness_cli_is_reusable_from_core_directory():
    core_dir = Path(__file__).parents[2]
    result = subprocess.run(
        [sys.executable, "scripts/sessiondb_replay_harness.py"],
        cwd=core_dir,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["replay"]["wal_journal_mode"] == "wal"
    assert "fixture-root" not in result.stdout
    assert str(core_dir) not in result.stdout
