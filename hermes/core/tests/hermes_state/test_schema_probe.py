"""Tests for the read-only Gate 2 schema capability probe."""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
from pathlib import Path

import pytest

from hermes_state_schema_probe import SchemaProbeResult, probe_schema


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_probe_reports_missing_objects_without_mutating_database():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "state.db"
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version(version) VALUES (11);
            CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL);
            """
        )
        connection.commit()
        connection.close()
        before = _digest(path)

        result = probe_schema(
            path,
            {
                "schema_version": ("version",),
                "sessions": ("id", "source", "session_key"),
                "messages": ("id",),
            },
        )

        assert isinstance(result, SchemaProbeResult)
        assert result.schema_version == 11
        assert result.missing_tables == ("messages",)
        assert result.missing_columns == (("sessions", "session_key"),)
        assert result.errors == ()
        assert result.ready is False
        assert _digest(path) == before


def test_probe_returns_ready_for_all_expected_columns():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "state.db"
        connection = sqlite3.connect(path)
        connection.executescript(
            """
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version(version) VALUES (11);
            CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL);
            """
        )
        connection.commit()
        connection.close()

        result = probe_schema(
            path,
            {"schema_version": ("version",), "sessions": ("id", "source")},
        )

        assert result == SchemaProbeResult(11, (), (), ())
        assert result.ready is True


def test_probe_does_not_create_sqlite_sidecars_for_closed_database():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "state.db"
        sqlite3.connect(path).close()

        probe_schema(path, {"sqlite_sequence": ()})

        assert not Path(f"{path}-wal").exists()
        assert not Path(f"{path}-shm").exists()


def test_probe_does_not_create_missing_database():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "missing.db"
        result = probe_schema(path, {"sessions": ("id",)})
        assert not path.exists()
        assert result.ready is False
        assert result.errors


def test_probe_rejects_sqlite_sidecar_symlink_without_following_it(tmp_path):
    path = tmp_path / "state.db"
    sqlite3.connect(path).close()
    target = tmp_path / "outside-wal"
    target.write_bytes(b"not a wal")
    sidecar = tmp_path / "state.db-wal"
    try:
        sidecar.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")

    result = probe_schema(path, {"sessions": ("id",)})

    assert result.ready is False
    assert any("sidecar symlink" in error for error in result.errors)


@pytest.mark.parametrize(
    "expected",
    [None, {"sessions": "id"}, {"bad-name": ("id",)}],
)
def test_probe_rejects_invalid_schema_description(expected):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "state.db"
        sqlite3.connect(path).close()
        with pytest.raises((TypeError, ValueError)):
            probe_schema(path, expected)  # type: ignore[arg-type]
