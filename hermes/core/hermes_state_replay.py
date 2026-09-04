"""Read-only, repeatable SessionDB compatibility replay.

The replay runner is an evidence tool for a copied SQLite database. It never
opens the live SessionDB connection, never runs DDL, and never calls the local
write transaction helper. Its purpose is to make the v11-to-v26 decision
repeatable before a future migration gate is considered.
"""

from __future__ import annotations

# Keep the repository bootstrap first among project imports. It configures
# UTF-8 stdio on Windows; the replay module itself remains read-only.
try:
    import hermes_bootstrap  # noqa: F401
except ModuleNotFoundError:
    hermes_bootstrap = None  # type: ignore[assignment]

import hashlib
import json
import os
import sqlite3
import stat
import tempfile
from dataclasses import dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from hermes_constants import get_hermes_home
from hermes_state_portability_compat import (
    MESSAGE_EXPORT_FIELDS,
    SESSION_EXPORT_FIELDS,
    audit_export_payload,
    import_sessions_into_db,
)
from hermes_state_schema_probe import probe_schema
from hermes_state_v26_compat import (
    LOCAL_V11_SCHEMA,
    V26_EXPECTED_SCHEMA,
    V26SchemaReport,
    build_v26_migration_plan,
    probe_v26_schema,
)


REPLAY_MAX_SESSIONS = 100
REPLAY_MAX_MESSAGES_PER_SESSION = 1_000
REPLAY_MAX_TOTAL_MESSAGES = 5_000
REPLAY_MAX_SEARCH_TERMS = 8
REPLAY_MAX_TERM_CHARS = 160
REPLAY_MAX_TEXT_CHARS = 100_000
REPLAY_MAX_HITS_PER_TERM = 10
REPLAY_MAX_ERROR_CHARS = 240
REPLAY_MAX_SOURCE_BYTES = 8 * 1024 * 1024 * 1024
REPLAY_MAX_AUDIT_ERRORS = 64
REPLAY_MAX_AUDIT_FIELDS = 128

_TEXT_COLUMNS = frozenset(
    {
        "source",
        "user_id",
        "model",
        "model_config",
        "system_prompt",
        "session_key",
        "chat_id",
        "chat_type",
        "thread_id",
        "display_name",
        "origin_json",
        "cwd",
        "git_branch",
        "git_repo_root",
        "billing_provider",
        "billing_base_url",
        "billing_mode",
        "cost_status",
        "cost_source",
        "pricing_version",
        "title",
        "title_source",
        "handoff_state",
        "handoff_platform",
        "handoff_error",
        "compression_failure_error",
        "profile_name",
        "system_prompt_hash",
        "last_activity_description",
        "last_activity_provenance",
        "effect_disposition",
        "_compressed_summary",
        "api_content",
        "display_kind",
        "role",
        "content",
        "tool_call_id",
        "tool_calls",
        "tool_name",
        "finish_reason",
        "reasoning",
        "reasoning_content",
        "reasoning_details",
        "codex_reasoning_items",
        "codex_message_items",
        "platform_message_id",
        "display_metadata",
    }
)

_JSON_COLUMNS = frozenset(
    {
        "model_config",
        "origin_json",
        "tool_calls",
        "reasoning_details",
        "codex_reasoning_items",
        "codex_message_items",
        "api_content",
        "display_metadata",
    }
)

_REPLAY_SESSION_FIELDS = tuple(
    dict.fromkeys(
        (
            *SESSION_EXPORT_FIELDS,
            *LOCAL_V11_SCHEMA["sessions"],
            *V26_EXPECTED_SCHEMA["sessions"],
        )
    )
)
_REPLAY_MESSAGE_FIELDS = tuple(
    dict.fromkeys(
        (
            *MESSAGE_EXPORT_FIELDS,
            *LOCAL_V11_SCHEMA["messages"],
            *V26_EXPECTED_SCHEMA["messages"],
        )
    )
)


def _safe_source_name(source_sha256: str) -> str:
    """Return a stable basename that cannot disclose the input filename."""

    digest = "".join(
        character
        for character in str(source_sha256).lower()
        if character in "0123456789abcdef"
    )[:16]
    return f"source-copy-{digest or 'unknown'}"


class ReplayInputError(ValueError):
    """The requested source is not an explicit safe database copy."""


@dataclass(frozen=True)
class ReplayReport:
    """Bounded JSON-serializable evidence from one replay pass."""

    accepted: bool
    source_name: str
    source_size: int = 0
    source_sha256: str = ""
    read_only: bool = True
    wal: Mapping[str, Any] = field(default_factory=dict)
    quick_check: tuple[str, ...] = ()
    integrity_ok: bool = False
    v11_probe: Mapping[str, Any] = field(default_factory=dict)
    v26_probe: Mapping[str, Any] = field(default_factory=dict)
    migration_plan: Mapping[str, Any] = field(default_factory=dict)
    export: Mapping[str, Any] = field(default_factory=dict)
    export_audit: Mapping[str, Any] = field(default_factory=dict)
    import_dry_run: Mapping[str, Any] = field(default_factory=dict)
    search_summary: Mapping[str, Any] = field(default_factory=dict)
    rollback: Mapping[str, Any] = field(default_factory=dict)
    errors: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        """Return a stable operator-facing status label."""

        if not self.accepted:
            return "rejected"
        return "ok" if not self.errors else "degraded"

    @property
    def ok(self) -> bool:
        """Return true only when the copy was accepted and replay had no errors."""

        return self.accepted and not self.errors

    @property
    def source(self) -> Mapping[str, Any]:
        """Return the source summary used by the legacy mapping API."""

        return {
            "name": _safe_source_name(self.source_sha256),
            "size_bytes": self.source_size,
            "sha256": self.source_sha256,
            "wal": self.wal,
        }

    @property
    def search(self) -> tuple[Mapping[str, Any], ...]:
        """Return legacy per-term search entries without message bodies."""

        terms = self.search_summary.get("terms", ())
        if not isinstance(terms, (tuple, list)):
            return ()
        return tuple(item for item in terms if isinstance(item, Mapping))

    @property
    def rollback_evidence(self) -> Mapping[str, Any]:
        """Return the legacy name for the canonical rollback evidence."""

        return self.rollback

    def to_dict(self) -> dict[str, Any]:
        """Return a detached JSON-compatible representation."""

        value = _jsonable(self)
        assert isinstance(value, dict)
        value["source_name"] = _safe_source_name(str(value.get("source_sha256") or ""))
        value["status"] = self.status
        value["ok"] = self.ok
        value["write_gate_open"] = False
        value["source"] = {
            "name": value["source_name"],
            "size_bytes": value.get("source_size", 0),
            "sha256": value.get("source_sha256", ""),
            "wal": value.get("wal", {}),
        }
        search_summary = value.get("search_summary", {})
        value["search"] = list(
            search_summary.get("terms", ())
            if isinstance(search_summary, Mapping)
            else ()
        )
        value["rollback_evidence"] = value.get("rollback", {})
        # The migration map used this name before the canonical field was
        # stabilized.  Keep both spellings so CLI and old callers observe the
        # same bounded export metadata.
        value["export_capture"] = value.get("export", {})
        return value


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {
            key: _jsonable(item)
            for key, item in value.__dict__.items()
        }
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return value.name[:160]
    return value


def _safe_error(error: BaseException, source: Path | None = None) -> str:
    """Bound diagnostics and remove source paths from the replay result."""

    detail = str(error).replace(chr(13), " ").replace(chr(10), " ")
    if source is not None:
        for candidate in (str(source), str(source.resolve(strict=False))):
            if candidate:
                detail = detail.replace(candidate, "<source-copy>")
    detail = detail[:REPLAY_MAX_ERROR_CHARS]
    return f"{type(error).__name__}: {detail}"


def _bounded_int(value: Any, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def _validate_source_copy(source_copy: str | Path) -> Path:
    """Resolve and validate an explicit regular-file copy without creating it."""

    if source_copy is None:
        raise ReplayInputError("source copy is required")
    try:
        path = Path(source_copy).expanduser()
    except (TypeError, ValueError) as error:
        raise ReplayInputError("source copy path is invalid") from error
    if path.is_symlink():
        raise ReplayInputError("source copy symlink paths are not accepted")
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except (FileNotFoundError, OSError) as error:
        raise ReplayInputError("source copy must be an existing regular file") from error
    if not stat.S_ISREG(info.st_mode):
        raise ReplayInputError("source copy must be an existing regular file")
    if int(info.st_size) > REPLAY_MAX_SOURCE_BYTES:
        raise ReplayInputError("source copy exceeds the replay size limit")

    # SQLite may follow WAL/journal sidecars while opening a database.  A
    # copied database is only safe to inspect when every present sidecar is a
    # bounded regular file owned by the copy, never a symlink or device.
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(f"{resolved}{suffix}")
        try:
            sidecar_info = sidecar.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise ReplayInputError("source copy sidecar could not be inspected") from error
        if stat.S_ISLNK(sidecar_info.st_mode):
            raise ReplayInputError("source copy sidecar symlink paths are not accepted")
        if not stat.S_ISREG(sidecar_info.st_mode):
            raise ReplayInputError("source copy sidecar must be a regular file")
        if int(sidecar_info.st_size) > REPLAY_MAX_SOURCE_BYTES:
            raise ReplayInputError("source copy sidecar exceeds the replay size limit")

    live_path = get_hermes_home() / "state.db"
    try:
        live_resolved = live_path.resolve(strict=False)
        if resolved == live_resolved:
            raise ReplayInputError(
                "current runtime state.db is not an accepted replay source"
            )
        if live_path.exists() and os.path.samefile(resolved, live_path):
            raise ReplayInputError(
                "current runtime state.db is not an accepted replay source"
            )
    except ReplayInputError:
        raise
    except OSError:
        # A missing live path is fine; the explicit source has already passed
        # regular-file validation and will still be read-only.
        pass
    return resolved


def _file_digest(path: Path, *, max_bytes: int = REPLAY_MAX_SOURCE_BYTES) -> str:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            total += len(chunk)
            if total > max_bytes:
                raise ReplayInputError("replay input exceeds the size limit")
            digest.update(chunk)
    return digest.hexdigest()


def _file_state(path: Path) -> dict[str, Any]:
    """Capture bounded hash/presence state for the DB and SQLite sidecars."""

    result: dict[str, Any] = {}
    for label, suffix in (
        ("main", ""),
        ("wal", "-wal"),
        ("shm", "-shm"),
        ("journal", "-journal"),
    ):
        candidate = path if not suffix else Path(str(path) + suffix)
        entry: dict[str, Any] = {"present": False, "size": 0, "sha256": ""}
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            result[label] = entry
            continue
        except OSError as error:
            entry["error"] = _safe_error(error, candidate)
            result[label] = entry
            continue
        entry["present"] = True
        entry["size"] = int(info.st_size)
        if stat.S_ISLNK(info.st_mode):
            entry["error"] = "symlink sidecar is not read"
        elif not stat.S_ISREG(info.st_mode):
            entry["error"] = "non-regular sidecar is not read"
        elif info.st_size > REPLAY_MAX_SOURCE_BYTES:
            entry["error"] = "sidecar exceeds replay size limit"
        else:
            try:
                entry["sha256"] = _file_digest(candidate)
            except OSError as error:
                entry["error"] = _safe_error(error, candidate)
        result[label] = entry
    return result


def _open_read_only(path: Path, *, immutable: bool = False) -> sqlite3.Connection:
    """Open a validated copy using SQLite URI read-only mode."""

    query = "mode=ro&immutable=1" if immutable else "mode=ro"
    connection = sqlite3.connect(
        f"{path.as_uri()}?{query}",
        uri=True,
        timeout=1.0,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _quick_check(connection: sqlite3.Connection) -> tuple[tuple[str, ...], str]:
    """Read journal mode and bounded PRAGMA quick_check output."""

    journal_row = connection.execute("PRAGMA journal_mode").fetchone()
    journal_mode = str(journal_row[0]).lower() if journal_row else "unknown"
    values = tuple(
        str(row[0])[:REPLAY_MAX_ERROR_CHARS]
        for row in connection.execute("PRAGMA quick_check(1)").fetchmany(1)
    )
    return values, journal_mode


def _quote_identifier(identifier: str) -> str:
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("database identifier is invalid")
    if chr(0) in identifier or any(
        not (char.isalnum() or char == "_") for char in identifier
    ):
        raise ValueError("database identifier is invalid")
    return f'"{identifier}"'


def _table_columns(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    quoted = _quote_identifier(table)
    rows = connection.execute(f"PRAGMA table_info({quoted})").fetchall()
    return tuple(str(row[1]) for row in rows)


def _select_expression(column: str) -> str:
    quoted = _quote_identifier(column)
    if column in _TEXT_COLUMNS:
        return f"substr({quoted}, 1, {REPLAY_MAX_TEXT_CHARS}) AS {quoted}"
    return quoted


def _decode_db_value(column: str, value: Any) -> Any:
    if column == "content" and isinstance(value, str):
        prefix = chr(0) + "json:"
        if value.startswith(prefix):
            try:
                return json.loads(value[len(prefix):])
            except (TypeError, ValueError):
                return value
    if column in _JSON_COLUMNS and isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value
    return value


def _row_to_payload(row: sqlite3.Row, columns: Sequence[str]) -> dict[str, Any]:
    return {
        column: _decode_db_value(column, row[column])
        for column in columns
    }


def _read_export(
    connection: sqlite3.Connection,
    *,
    max_sessions: int,
    max_messages_per_session: int,
    max_total_messages: int,
) -> dict[str, Any]:
    """Read a bounded portability-shaped export from a read-only connection."""

    available_tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "sessions" not in available_tables:
        return {
            "payload": [],
            "total_sessions": 0,
            "exported_sessions": 0,
            "total_messages": 0,
            "exported_messages": 0,
            "truncated": False,
            "missing_tables": ["sessions"],
        }

    session_available = _table_columns(connection, "sessions")
    session_columns = tuple(
        column for column in _REPLAY_SESSION_FIELDS
        if column in session_available
    )
    if "id" not in session_columns:
        return {
            "payload": [],
            "total_sessions": 0,
            "exported_sessions": 0,
            "total_messages": 0,
            "exported_messages": 0,
            "truncated": False,
            "missing_columns": ["sessions.id"],
        }

    total_sessions = int(
        connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    )
    order_columns = [
        column for column in ("started_at", "id")
        if column in session_available
    ]
    order_sql = ", ".join(
        f"{_quote_identifier(column)} DESC" for column in order_columns
    ) or _quote_identifier("id")
    select_sql = ", ".join(_select_expression(column) for column in session_columns)
    session_rows = connection.execute(
        f"SELECT {select_sql} FROM {_quote_identifier('sessions')} "
        f"ORDER BY {order_sql} LIMIT ?",
        (max_sessions,),
    ).fetchall()

    message_available = (
        _table_columns(connection, "messages")
        if "messages" in available_tables
        else ()
    )
    message_columns = tuple(
        column for column in _REPLAY_MESSAGE_FIELDS
        if column in message_available
    )
    can_read_messages = {"session_id", "role"} <= set(message_columns)
    total_messages = (
        int(connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
        if "messages" in available_tables
        else 0
    )
    payload: list[dict[str, Any]] = []
    exported_messages = 0
    truncated = total_sessions > len(session_rows)

    for session_row in session_rows:
        session = _row_to_payload(session_row, session_columns)
        session_id = session.get("id")
        messages: list[dict[str, Any]] = []
        if can_read_messages and session_id is not None and exported_messages < max_total_messages:
            order = [
                column for column in ("timestamp", "id")
                if column in message_available
            ]
            message_order = ", ".join(
                f"{_quote_identifier(column)} ASC" for column in order
            ) or _quote_identifier("role")
            message_select = ", ".join(
                _select_expression(column) for column in message_columns
            )
            remaining = min(
                max_messages_per_session,
                max_total_messages - exported_messages,
            )
            rows = connection.execute(
                f"SELECT {message_select} FROM {_quote_identifier('messages')} "
                f"WHERE {_quote_identifier('session_id')} = ? "
                f"ORDER BY {message_order} LIMIT ?",
                (session_id, remaining),
            ).fetchall()
            messages = [
                _row_to_payload(row, message_columns)
                for row in rows
            ]
            exported_messages += len(messages)
        session["messages"] = messages
        payload.append(session)

    if total_messages > exported_messages:
        truncated = True
    return {
        "payload": payload,
        "total_sessions": total_sessions,
        "exported_sessions": len(payload),
        "total_messages": total_messages,
        "exported_messages": exported_messages,
        "truncated": truncated,
    }


def _probe_dict(result: Any) -> dict[str, Any]:
    output = {
        "schema_version": getattr(result, "schema_version", None),
        "missing_tables": tuple(getattr(result, "missing_tables", ())),
        "missing_columns": tuple(getattr(result, "missing_columns", ())),
        "errors": tuple(getattr(result, "errors", ())),
    }
    if isinstance(result, V26SchemaReport):
        output.update(
            {
                "state": result.state,
                "is_v26_ready": result.is_v26_ready,
                "migration_required": result.migration_required,
            }
        )
    else:
        output["ready"] = bool(getattr(result, "ready", False))
    return output


def _plan_dict(plan: Any) -> dict[str, Any]:
    return {
        "source_state": plan.source_state,
        "source_schema_version": plan.source_schema_version,
        "target_schema_version": plan.target_schema_version,
        "add_tables": tuple(plan.add_tables),
        "add_columns": tuple(plan.add_columns),
        "blockers": tuple(plan.blockers),
        "deferred_operations": tuple(plan.deferred_operations),
        "next_steps": tuple(plan.next_steps),
        "requires_copied_database": plan.requires_copied_database,
        "write_gate_open": plan.write_gate_open,
        "is_read_only": plan.is_read_only,
        "requires_migration": plan.requires_migration,
        "replayable_on_copy": plan.replayable_on_copy,
    }


def _audit_dict(audit: Any) -> dict[str, Any]:
    unknown_session_fields = tuple(
        str(value)[:REPLAY_MAX_ERROR_CHARS]
        for value in tuple(audit.unknown_session_fields)[:REPLAY_MAX_AUDIT_FIELDS]
    )
    unknown_message_fields = tuple(
        str(value)[:REPLAY_MAX_ERROR_CHARS]
        for value in tuple(audit.unknown_message_fields)[:REPLAY_MAX_AUDIT_FIELDS]
    )
    errors = tuple(
        str(value)[:REPLAY_MAX_ERROR_CHARS]
        for value in tuple(audit.errors)[:REPLAY_MAX_AUDIT_ERRORS]
    )
    return {
        "ok": bool(audit.ok),
        "session_count": int(audit.session_count),
        "message_count": int(audit.message_count),
        "total_bytes": int(audit.total_bytes),
        "unknown_session_fields": unknown_session_fields,
        "unknown_message_fields": unknown_message_fields,
        "unknown_fields_truncated": (
            len(tuple(audit.unknown_session_fields)) > REPLAY_MAX_AUDIT_FIELDS
            or len(tuple(audit.unknown_message_fields)) > REPLAY_MAX_AUDIT_FIELDS
        ),
        "errors": errors,
        "errors_truncated": len(tuple(audit.errors)) > REPLAY_MAX_AUDIT_ERRORS,
    }


def _bounded_error_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {"error": str(value)[:REPLAY_MAX_ERROR_CHARS]}
    return {
        str(key)[:80]: str(item)[:REPLAY_MAX_ERROR_CHARS]
        for key, item in list(value.items())[:16]
    }


def _import_dry_run_dict(result: Any) -> dict[str, Any]:
    return {
        "ok": bool(result.ok),
        "status": str(result.status),
        "would_import_count": len(result.imported_ids),
        "imported_id_hashes": tuple(
            hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()[:16]
            for value in result.imported_ids[:REPLAY_MAX_SESSIONS]
        ),
        "skipped_id_hashes": tuple(
            hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()[:16]
            for value in result.skipped_ids[:REPLAY_MAX_SESSIONS]
        ),
        "detached_count": int(result.detached_count),
        "errors": tuple(
            _bounded_error_mapping(item) for item in result.errors[:20]
        ),
    }


_SAFE_REPORT_SOURCES = frozenset(
    {
        "acp",
        "cli",
        "gateway",
        "import",
        "onebot",
        "system",
        "web",
    }
)


def _report_source(value: Any) -> str:
    """Expose only known source labels in a report, never arbitrary text."""

    source = str(value or "").strip().lower()
    return source if source in _SAFE_REPORT_SOURCES else "<redacted>"


def _search_summary(
    connection: sqlite3.Connection,
    terms: Iterable[str],
) -> dict[str, Any]:
    """Search canonical message content with literal, bounded substring probes."""

    normalized_terms: list[str] = []
    if isinstance(terms, str):
        terms = (terms,)
    for term in terms:
        if not isinstance(term, str):
            continue
        value = term.strip()[:REPLAY_MAX_TERM_CHARS]
        if value:
            normalized_terms.append(value)
        if len(normalized_terms) >= REPLAY_MAX_SEARCH_TERMS:
            break
    available = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if not {"sessions", "messages"} <= available:
        return {
            "method": "canonical_hex_instr",
            "terms": [],
            "skipped": "sessions/messages table unavailable",
        }

    message_columns = set(_table_columns(connection, "messages"))
    if "content" not in message_columns:
        return {
            "method": "canonical_hex_instr",
            "terms": [],
            "skipped": "messages.content column unavailable",
        }
    session_columns = set(_table_columns(connection, "sessions"))

    # Keep the diagnostic query bounded even when a malformed historical row
    # contains a very large blob.  The replay export applies the same bound;
    # search intentionally covers only the captured prefix.
    bounded_content = (
        f"substr(CAST(m.content AS BLOB), 1, {REPLAY_MAX_TEXT_CHARS})"
    )
    count_content = (
        f"substr(CAST(content AS BLOB), 1, {REPLAY_MAX_TEXT_CHARS})"
    )
    message_id = 'm."id"' if "id" in message_columns else "NULL"
    session_id = 'm."session_id"' if "session_id" in message_columns else "NULL"
    role = 'm."role"' if "role" in message_columns else "NULL"
    if {"id", "source"} <= session_columns and "session_id" in message_columns:
        source = 's."source"'
        join = ' LEFT JOIN "sessions" AS s ON s."id" = m."session_id"'
    else:
        source = "NULL"
        join = ""
    order_parts: list[str] = []
    if "timestamp" in message_columns:
        order_parts.append('m."timestamp" DESC')
    if "id" in message_columns:
        order_parts.append('m."id" DESC')
    order_sql = ", ".join(order_parts) or "1"

    results: list[dict[str, Any]] = []
    for term in normalized_terms:
        needle = term.encode("utf-8", errors="replace").hex().upper()
        count = int(
            connection.execute(
                "SELECT COUNT(*) FROM messages "
                f"WHERE instr(upper(hex({count_content})), ?) > 0",
                (needle,),
            ).fetchone()[0]
        )
        rows = connection.execute(
            f"SELECT {message_id} AS message_id, {session_id} AS session_id, "
            f"{role} AS role, {source} AS source "
            f"FROM \"messages\" AS m{join} "
            f"WHERE instr(upper(hex({bounded_content})), ?) > 0 "
            f"ORDER BY {order_sql} LIMIT ?",
            (needle, REPLAY_MAX_HITS_PER_TERM),
        ).fetchall()
        results.append(
            {
                "term_chars": len(term),
                "term_sha256": hashlib.sha256(term.encode("utf-8")).hexdigest()[:16],
                "match_count": count,
                "hits": [
                    {
                        "message_id_hash": hashlib.sha256(
                            str(row["message_id"]).encode("utf-8", errors="replace")
                        ).hexdigest()[:16],
                        "session_id_hash": hashlib.sha256(
                            str(row["session_id"]).encode("utf-8", errors="replace")
                        ).hexdigest()[:16],
                        "role": str(row["role"] or "")[:40],
                        "source": _report_source(row["source"]),
                    }
                    for row in rows
                ],
            }
        )
    return {
        "method": "canonical_hex_instr",
        "terms": results,
    }


def _rollback_evidence(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    *,
    tolerate_wal_shm_read_locks: bool = False,
    journal_mode: str = "",
) -> dict[str, Any]:
    main_before = before.get("main", {})
    main_after = after.get("main", {})
    sidecars = ("wal", "shm", "journal")
    main_unchanged = (
        main_before.get("sha256") == main_after.get("sha256")
        and main_before.get("size") == main_after.get("size")
    )
    sidecars_unchanged = all(
        before.get(name, {}) == after.get(name, {})
        for name in sidecars
    )
    wal_journal_sidecars_unchanged = all(
        before.get(name, {}) == after.get(name, {})
        for name in ("wal", "journal")
    )
    shm_changed = before.get("shm", {}) != after.get("shm", {})
    before_shm = before.get("shm", {})
    after_shm = after.get("shm", {})
    shm_tolerated = bool(
        tolerate_wal_shm_read_locks
        and str(journal_mode).lower() == "wal"
        and shm_changed
        and before.get("wal", {}).get("present")
        and after.get("wal", {}).get("present")
        and before_shm.get("present")
        and after_shm.get("present")
        and before_shm.get("size") == after_shm.get("size")
        and not before_shm.get("error")
        and not after_shm.get("error")
    )
    unchanged = bool(
        main_unchanged
        and wal_journal_sidecars_unchanged
        and (not shm_changed or shm_tolerated)
    )
    return {
        "checked": True,
        "unchanged": unchanged,
        "main_sha256_before": main_before.get("sha256", ""),
        "main_sha256_after": main_after.get("sha256", ""),
        "sidecars_unchanged": sidecars_unchanged,
        "main_wal_journal_unchanged": bool(
            main_unchanged and wal_journal_sidecars_unchanged
        ),
        "shm_changed_during_read": shm_changed,
        "shm_read_lock_change_tolerated": shm_tolerated,
        "source_unchanged": unchanged,
        "write_operations": 0,
        "method": "sha256_main_and_sqlite_sidecars",
    }


def run_replay(
    source_copy: str | Path,
    *,
    search_terms: Sequence[str] = (),
    max_sessions: int = REPLAY_MAX_SESSIONS,
    max_messages_per_session: int = REPLAY_MAX_MESSAGES_PER_SESSION,
    max_total_messages: int = REPLAY_MAX_TOTAL_MESSAGES,
    tolerate_wal_shm_read_locks: bool = False,
) -> ReplayReport:
    """Run all read-only replay checks against an explicit database copy.

    ``tolerate_wal_shm_read_locks`` is an explicit disposable-replay option.
    SQLite may update the ``-shm`` lock/index sidecar when a second read-only
    process opens a WAL database even though the canonical database and WAL
    frames remain unchanged. The default stays strict; callers that use this
    option must still pass the main/WAL/journal stability checks, and the
    report records the tolerated ``-shm`` mutation separately.
    """

    path = _validate_source_copy(source_copy)
    max_sessions = _bounded_int(max_sessions, REPLAY_MAX_SESSIONS, REPLAY_MAX_SESSIONS)
    max_messages_per_session = _bounded_int(
        max_messages_per_session,
        REPLAY_MAX_MESSAGES_PER_SESSION,
        REPLAY_MAX_MESSAGES_PER_SESSION,
    )
    max_total_messages = _bounded_int(
        max_total_messages,
        REPLAY_MAX_TOTAL_MESSAGES,
        REPLAY_MAX_TOTAL_MESSAGES,
    )

    before = _file_state(path)
    errors: list[str] = []
    quick_values: tuple[str, ...] = ()
    journal_mode = "unknown"
    integrity_ok = False
    export_info: dict[str, Any] = {
        "payload": [],
        "total_sessions": 0,
        "exported_sessions": 0,
        "total_messages": 0,
        "exported_messages": 0,
        "truncated": False,
    }
    audit_info: dict[str, Any] = {}
    import_info: dict[str, Any] = {}
    search_info: dict[str, Any] = {}
    v11_info: dict[str, Any] = {}
    v26_info: dict[str, Any] = {}
    plan_info: dict[str, Any] = {}
    connection: sqlite3.Connection | None = None

    try:
        connection = _open_read_only(
            path,
            immutable=not any(
                bool(before.get(name, {}).get("present"))
                for name in ("wal", "shm", "journal")
            ),
        )
        quick_values, journal_mode = _quick_check(connection)
        integrity_ok = bool(quick_values) and all(
            value.lower() == "ok" for value in quick_values
        )
        export_info = _read_export(
            connection,
            max_sessions=max_sessions,
            max_messages_per_session=max_messages_per_session,
            max_total_messages=max_total_messages,
        )
        search_info = _search_summary(connection, search_terms)
    except Exception as error:
        errors.append(_safe_error(error, path))
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error as error:
                errors.append(_safe_error(error, path))

    try:
        v11_info = _probe_dict(probe_schema(path, LOCAL_V11_SCHEMA))
    except Exception as error:
        errors.append(_safe_error(error, path))
    try:
        v26_report = probe_v26_schema(path)
        v26_info = _probe_dict(v26_report)
        plan_info = _plan_dict(build_v26_migration_plan(v26_report))
    except Exception as error:
        errors.append(_safe_error(error, path))

    payload = export_info.get("payload", [])
    try:
        audit = audit_export_payload(
            payload,
            max_sessions=max_sessions,
            max_messages_per_session=max_messages_per_session,
            max_total_messages=max_total_messages,
        )
        audit_info = _audit_dict(audit)
        dry_run = import_sessions_into_db(
            None,
            payload,
            enable=True,
            dry_run=True,
        )
        import_info = _import_dry_run_dict(dry_run)
    except Exception as error:
        errors.append(_safe_error(error, path))

    after = _file_state(path)
    rollback = _rollback_evidence(
        before,
        after,
        tolerate_wal_shm_read_locks=bool(tolerate_wal_shm_read_locks),
        journal_mode=journal_mode,
    )
    if not rollback["unchanged"]:
        errors.append("source copy changed during read-only replay")

    main_state = before.get("main", {})
    wal_state = before.get("wal", {})
    wal = {
        "journal_mode": journal_mode,
        "wal_present": bool(wal_state.get("present")),
        "wal_size": int(wal_state.get("size") or 0),
        "wal_sha256": wal_state.get("sha256", ""),
        "shm_changed_during_read": bool(
            rollback.get("shm_changed_during_read", False)
        ),
        "shm_read_lock_change_tolerated": bool(
            rollback.get("shm_read_lock_change_tolerated", False)
        ),
        "sidecar_errors": tuple(
            f"{name}: {entry['error']}"
            for name, entry in before.items()
            if isinstance(entry, Mapping) and entry.get("error")
        ),
    }
    return ReplayReport(
        accepted=True,
        source_name=_safe_source_name(str(main_state.get("sha256") or "")),
        source_size=int(main_state.get("size") or 0),
        source_sha256=str(main_state.get("sha256") or ""),
        wal=wal,
        quick_check=quick_values,
        integrity_ok=integrity_ok,
        v11_probe=v11_info,
        v26_probe=v26_info,
        migration_plan=plan_info,
        export={
            key: value
            for key, value in export_info.items()
            if key != "payload"
        },
        export_audit=audit_info,
        import_dry_run=import_info,
        search_summary=search_info,
        rollback=rollback,
        errors=tuple(dict.fromkeys(errors)),
    )


def replay_database(
    source: str | Path,
    *,
    queries: Iterable[str] = (),
) -> dict[str, Any]:
    """Compatibility wrapper returning the canonical replay report mapping."""

    report = run_replay(source, search_terms=tuple(queries))
    payload = report.to_dict()
    # Preserve the earlier diagnostic wrapper shape for callers that already
    # consume this helper, while keeping the canonical ReplayReport fields.
    payload["source"] = {
        "name": report.source_name,
        "size_bytes": report.source_size,
        "sha256": report.source_sha256,
        "wal": payload.get("wal", {}),
    }
    rollback = dict(payload.get("rollback", {}))
    rollback["source_unchanged"] = bool(rollback.get("unchanged", False))
    rollback["write_operations"] = 0
    payload["rollback_evidence"] = rollback
    payload["search"] = list(
        payload.get("search_summary", {}).get("terms", [])
        if isinstance(payload.get("search_summary"), Mapping)
        else []
    )
    return payload


def write_replay_report(report: ReplayReport | Mapping[str, Any], output: str | Path) -> Path:
    """Atomically write a replay report without overwriting its source copy."""

    if isinstance(report, ReplayReport):
        payload = report.to_dict()
        source_hash = report.source_sha256
    elif isinstance(report, Mapping):
        payload = dict(report)
        source_hash = str(payload.get("source_sha256") or "")
        if not source_hash:
            source = payload.get("source")
            if isinstance(source, Mapping):
                source_hash = str(source.get("sha256") or "")
    else:
        raise TypeError("report must be a ReplayReport or mapping")

    target = Path(output).expanduser().resolve()
    if target.exists() and source_hash:
        try:
            if _file_digest(target) == source_hash:
                raise ReplayInputError(
                    "refusing to overwrite the replay source database with its report"
                )
        except ReplayInputError:
            raise
        except (OSError, ValueError):
            pass
    live = (get_hermes_home() / "state.db").expanduser().resolve(strict=False)
    if target == live:
        raise ReplayInputError("refusing to write a replay report over runtime state.db")

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    descriptor: int | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.tmp-",
            dir=str(target.parent),
            text=True,
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = None
            stream.write(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n"
            )
        os.replace(temporary, target)
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return target


__all__ = [
    "ReplayInputError",
    "ReplayReport",
    "REPLAY_MAX_MESSAGES_PER_SESSION",
    "REPLAY_MAX_SESSIONS",
    "REPLAY_MAX_TOTAL_MESSAGES",
    "replay_database",
    "run_replay",
    "write_replay_report",
]
