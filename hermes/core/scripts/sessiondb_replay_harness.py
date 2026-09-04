"""Run a disposable SessionDB replay/WAL contract on a synthetic copy.

This runner never discovers or opens the runtime ``state.db``. It creates a
temporary v11-shaped database, exercises a compression parent/child lineage
with a deliberately late event, keeps a separate WAL writer process alive,
and invokes the existing read-only replay CLI from another process. The
result is bounded status evidence only; paths, session IDs, and message bodies
are intentionally omitted.

Run from ``hermes/core``::

    python scripts/sessiondb_replay_harness.py

The same command is suitable for Windows, WSL, and Linux with Python 3.11+
and a standard ``sqlite3`` build that supports WAL. It does not perform a
migration, copy a caller-selected database, or leave a fixture behind.
"""

from __future__ import annotations

# A direct ``python scripts/sessiondb_replay_harness.py`` invocation sets
# ``sys.path[0]`` to ``scripts/`` rather than the core directory.  Resolve the
# project root before importing Hermes modules so the documented CLI works on
# both Windows and POSIX without relying on the caller's current directory.
import sys
from pathlib import Path

_CORE_DIR = Path(__file__).resolve().parents[1]
if str(_CORE_DIR) not in sys.path:
    sys.path.insert(0, str(_CORE_DIR))

try:
    import hermes_bootstrap  # noqa: F401
except ModuleNotFoundError:
    hermes_bootstrap = None  # type: ignore[assignment]

import hashlib
import json
import platform
import queue
import sqlite3
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Any, Mapping

from hermes_state import SessionDB


HARNESS_TIMEOUT_SECONDS = 15.0
HARNESS_ERROR_CHARS = 240


# The writer intentionally uses only the stdlib so the process boundary also
# proves that no in-process SessionDB singleton or connection is being reused.
_WAL_WRITER_PROGRAM = r'''
import sqlite3
import sys

database = sys.argv[1]
connection = sqlite3.connect(database, timeout=2.0)
try:
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) "
        "VALUES (?, ?, ?, ?)",
        ("fixture-child", "user", "wal probe marker", 400.0),
    )
    connection.commit()
    print("ready", flush=True)
    sys.stdin.readline()
finally:
    connection.close()
'''


@dataclass(frozen=True)
class HarnessReport:
    """Stable, path-free evidence from one disposable harness run."""

    ok: bool
    status: str
    platform: str
    fixture: Mapping[str, Any] = field(default_factory=dict)
    lineage: Mapping[str, Any] = field(default_factory=dict)
    wal: Mapping[str, Any] = field(default_factory=dict)
    replay: Mapping[str, Any] = field(default_factory=dict)
    cleanup: Mapping[str, Any] = field(default_factory=dict)
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return a detached JSON-compatible, path-free mapping."""

        return {
            "ok": bool(self.ok),
            "status": self.status,
            "platform": self.platform,
            "fixture": _jsonable(self.fixture),
            "lineage": _jsonable(self.lineage),
            "wal": _jsonable(self.wal),
            "replay": _jsonable(self.replay),
            "cleanup": _jsonable(self.cleanup),
            "errors": list(self.errors),
        }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _safe_error(error: BaseException, *paths: Path) -> str:
    """Bound an error without allowing temporary paths into the report."""

    detail = str(error).replace(chr(13), " ").replace(chr(10), " ")
    for path in paths:
        try:
            detail = detail.replace(str(path), "<temporary-copy>")
            detail = detail.replace(str(path.resolve(strict=False)), "<temporary-copy>")
        except (OSError, ValueError):
            pass
    return f"{type(error).__name__}: {detail[:HARNESS_ERROR_CHARS]}"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sidecar_state(path: Path) -> dict[str, Any]:
    """Capture only presence, size, and digest for a synthetic copy."""

    result: dict[str, Any] = {}
    for label, suffix in (("wal", "-wal"), ("shm", "-shm"), ("journal", "-journal")):
        candidate = Path(str(path) + suffix)
        if not candidate.exists():
            result[label] = {"present": False, "size": 0, "sha256": ""}
            continue
        try:
            result[label] = {
                "present": True,
                "size": int(candidate.stat().st_size),
                "sha256": _file_sha256(candidate),
            }
        except OSError:
            result[label] = {"present": True, "size": -1, "sha256": ""}
    return result


def _create_synthetic_fixture(path: Path) -> dict[str, Any]:
    """Create a v11 fixture and verify lineage/late-event ordering locally."""

    database = SessionDB(db_path=path)
    try:
        root_id = "fixture-root"
        child_id = "fixture-child"
        database.create_session(root_id, source="fixture", model="test-model")
        database.create_session(
            child_id,
            source="fixture",
            model="test-model",
            parent_session_id=root_id,
        )
        with database._lock:
            database._conn.execute(
                "UPDATE sessions SET started_at = ?, ended_at = ?, "
                "end_reason = 'compression' WHERE id = ?",
                (100.0, 200.0, root_id),
            )
            database._conn.execute(
                "UPDATE sessions SET started_at = ? WHERE id = ?",
                (201.0, child_id),
            )
            database._conn.commit()

        database.append_message(root_id, "user", "parent event", timestamp=100.0)
        database.append_message(child_id, "user", "current event", timestamp=300.0)
        # This event arrives late, but remains canonical and ordered by its
        # supplied event timestamp rather than being dropped or re-anchored.
        database.append_message(child_id, "user", "late event", timestamp=250.0)

        replayed = database.get_messages_as_conversation(
            child_id,
            include_ancestors=True,
        )
        ordered_contents = [item.get("content") for item in replayed]
        lineage_ok = database.get_compression_tip(root_id) == child_id
        late_event_ok = ordered_contents == [
            "parent event",
            "late event",
            "current event",
        ]
        with database._lock:
            version_row = database._conn.execute(
                "SELECT version FROM schema_version LIMIT 1"
            ).fetchone()
            v26_table_count = int(
                database._conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' "
                    "AND name IN ('gateway_routing', 'session_turn_leases', 'async_delegations')"
                ).fetchone()[0]
            )
        return {
            "schema_version": int(version_row[0]) if version_row else 0,
            "v26_runtime_tables": v26_table_count,
            "lineage_ok": bool(lineage_ok),
            "late_event_ok": bool(late_event_ok),
            "message_count": len(replayed),
        }
    finally:
        database.close()


def _read_ready_line(process: subprocess.Popen[str], timeout: float) -> str:
    """Read one child status line with a timeout on Windows and POSIX."""

    if process.stdout is None:
        return ""
    ready: queue.Queue[str] = queue.Queue(maxsize=1)

    def _reader() -> None:
        try:
            ready.put(process.stdout.readline())
        except Exception:
            try:
                ready.put("")
            except queue.Full:
                pass

    threading.Thread(target=_reader, daemon=True).start()
    try:
        return ready.get(timeout=timeout).strip()
    except queue.Empty:
        return ""


def _stop_writer(process: subprocess.Popen[str] | None) -> int | None:
    """Release or terminate the bounded synthetic writer process."""

    if process is None:
        return None
    try:
        if process.stdin is not None:
            process.stdin.write("\n")
            process.stdin.flush()
        process.communicate(timeout=HARNESS_TIMEOUT_SECONDS)
    except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.communicate(timeout=5.0)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return process.returncode


def run_disposable_harness() -> HarnessReport:
    """Run the Windows-first disposable lineage/WAL/replay contract."""

    errors: list[str] = []
    fixture_info: dict[str, Any] = {}
    lineage_info: dict[str, Any] = {}
    wal_info: dict[str, Any] = {}
    replay_info: dict[str, Any] = {}
    cleanup_info: dict[str, Any] = {}
    writer: subprocess.Popen[str] | None = None
    temporary = tempfile.TemporaryDirectory(prefix="hermes-replay-harness-")
    database_path = Path(temporary.name) / "fixture.db"
    report_path = Path(temporary.name) / "replay-report.json"

    try:
        fixture_info = _create_synthetic_fixture(database_path)
        lineage_info = {
            "compression_parent_child": fixture_info.get("lineage_ok", False),
            "late_event_order": fixture_info.get("late_event_ok", False),
        }

        writer = subprocess.Popen(
            [sys.executable, "-c", _WAL_WRITER_PROGRAM, str(database_path)],
            cwd=str(Path(__file__).resolve().parents[1]),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if _read_ready_line(writer, HARNESS_TIMEOUT_SECONDS) != "ready":
            errors.append(_safe_error(RuntimeError("WAL writer did not become ready"), database_path))
        else:
            replay_before = _file_sha256(database_path)
            replay_sidecars_before = _sidecar_state(database_path)
            try:
                child = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "scripts.sessiondb_replay",
                        "--source",
                        str(database_path),
                        "--query",
                        "wal probe marker",
                        "--tolerate-wal-shm-read-locks",
                        "--output",
                        str(report_path),
                    ],
                    cwd=str(Path(__file__).resolve().parents[1]),
                    capture_output=True,
                    text=True,
                    timeout=HARNESS_TIMEOUT_SECONDS,
                    check=False,
                )
                if child.returncode != 0:
                    errors.append(
                        _safe_error(
                            RuntimeError("replay subprocess returned a failure"),
                            database_path,
                            report_path,
                        )
                    )
                if report_path.is_file():
                    payload = json.loads(report_path.read_text(encoding="utf-8"))
                    search_rows = payload.get("search", [])
                    wal_payload = payload.get("wal", {})
                    rollback = payload.get("rollback_evidence", {})
                    match_count = 0
                    if isinstance(search_rows, list) and search_rows:
                        match_count = int(search_rows[0].get("match_count", 0) or 0)
                    journal_mode = str(wal_payload.get("journal_mode", "")).lower()
                    source_unchanged = bool(rollback.get("source_unchanged"))
                    replay_info = {
                        "subprocess_ok": child.returncode == 0,
                        "report_ok": bool(payload.get("ok")),
                        "read_only": bool(payload.get("read_only")),
                        "write_gate_open": bool(payload.get("write_gate_open")),
                        "wal_journal_mode": journal_mode,
                        "wal_probe_matches": match_count,
                        "source_unchanged": source_unchanged,
                        "shm_changed_during_read": bool(
                            wal_payload.get("shm_changed_during_read", False)
                        ),
                        "shm_read_lock_change_tolerated": bool(
                            wal_payload.get(
                                "shm_read_lock_change_tolerated", False
                            )
                        ),
                    }
                else:
                    errors.append(_safe_error(FileNotFoundError("replay report missing"), report_path))
            except (OSError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError) as error:
                errors.append(_safe_error(error, database_path, report_path))

            replay_after = _file_sha256(database_path)
            replay_sidecars_after = _sidecar_state(database_path)
            main_wal_unchanged = (
                replay_before == replay_after
                and all(
                    replay_sidecars_before.get(name, {})
                    == replay_sidecars_after.get(name, {})
                    for name in ("wal", "journal")
                )
            )
            wal_path = Path(str(database_path) + "-wal")
            wal_info = {
                "writer_ready": True,
                "writer_wal_sidecar_present": wal_path.exists(),
                "source_main_wal_unchanged_after_replay": main_wal_unchanged,
                "reader_shm_changed": (
                    replay_sidecars_before.get("shm", {})
                    != replay_sidecars_after.get("shm", {})
                ),
            }
            if not main_wal_unchanged:
                errors.append("synthetic source changed during replay")
    except (OSError, sqlite3.Error, ValueError) as error:
        errors.append(_safe_error(error, database_path, report_path))
    finally:
        writer_exit = _stop_writer(writer)
        wal_info["writer_exit_code"] = writer_exit
        source_exists_before_cleanup = database_path.exists()
        temporary.cleanup()
        cleanup_info = {
            "temporary_disposed": True,
            "source_removed": source_exists_before_cleanup and not database_path.exists(),
            "report_removed": not report_path.exists(),
        }

    all_contracts_ok = (
        fixture_info.get("schema_version") == 11
        and fixture_info.get("v26_runtime_tables") == 0
        and lineage_info.get("compression_parent_child") is True
        and lineage_info.get("late_event_order") is True
        and wal_info.get("writer_ready") is True
        and wal_info.get("writer_exit_code") == 0
        and wal_info.get("source_main_wal_unchanged_after_replay") is True
        and replay_info.get("subprocess_ok") is True
        and replay_info.get("report_ok") is True
        and replay_info.get("read_only") is True
        and replay_info.get("write_gate_open") is False
        and replay_info.get("wal_journal_mode") == "wal"
        and replay_info.get("wal_probe_matches") == 1
        and replay_info.get("source_unchanged") is True
        and cleanup_info.get("source_removed") is True
        and cleanup_info.get("report_removed") is True
        and not errors
    )
    return HarnessReport(
        ok=all_contracts_ok,
        status="ok" if all_contracts_ok else "degraded",
        platform=platform.system().lower() or "unknown",
        fixture=fixture_info,
        lineage=lineage_info,
        wal=wal_info,
        replay=replay_info,
        cleanup=cleanup_info,
        errors=tuple(dict.fromkeys(errors)),
    )


def main(argv: list[str] | None = None) -> int:
    """Run the disposable contract and print bounded JSON evidence."""

    # Keep a deliberately tiny CLI: there is no source-path argument, so an
    # accidental invocation cannot turn this disposable runner into a history
    # scanner. Use scripts/sessiondb_replay.py for an explicitly authorized
    # copy instead.
    report = run_disposable_harness()
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["HARNESS_TIMEOUT_SECONDS", "HarnessReport", "main", "run_disposable_harness"]
