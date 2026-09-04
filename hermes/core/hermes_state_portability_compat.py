"""Read-only portability audit helpers for the local SessionDB facade.

This module deliberately does not import ``hermes_state`` and never opens a
database.  It audits exported dictionaries before a future import path can
write them, enforcing the upstream size/shape contract without changing the
current v11 export or migration behavior.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple


IMPORT_MAX_SESSIONS = 500
IMPORT_MAX_MESSAGES_PER_SESSION = 10_000
IMPORT_MAX_TOTAL_MESSAGES = 50_000
IMPORT_MAX_SESSION_BYTES = 5 * 1024 * 1024
IMPORT_MAX_TOTAL_BYTES = 25 * 1024 * 1024
MAX_SESSION_ID_CHARS = 240

# Fields emitted by the local v11 facade plus the optional fields used by the
# upstream portability mixin. Unknown fields are reported, never copied into
# a future SQL statement.
SESSION_EXPORT_FIELDS = frozenset(
    {
        "id", "source", "user_id", "model", "model_config", "system_prompt",
        "parent_session_id", "started_at", "ended_at", "end_reason",
        "message_count", "tool_call_count", "input_tokens", "output_tokens",
        "cache_read_tokens", "cache_write_tokens", "reasoning_tokens",
        "billing_provider", "billing_base_url", "billing_mode",
        "estimated_cost_usd", "actual_cost_usd", "cost_status", "cost_source",
        "pricing_version", "title", "api_call_count", "archived",
        "messages", "segments", "lineage_session_ids", "preview", "last_active",
    }
)
MESSAGE_EXPORT_FIELDS = frozenset(
    {
        "id", "session_id", "role", "content", "tool_call_id", "tool_calls",
        "tool_name", "timestamp", "token_count", "finish_reason", "reasoning",
        "reasoning_content", "reasoning_details", "codex_reasoning_items",
        "codex_message_items", "effect_disposition", "platform_message_id",
        "message_id", "_compressed_summary",
    }
)


@dataclass(frozen=True)
class PortabilityAudit:
    """Immutable result of auditing one export payload."""

    ok: bool
    session_count: int = 0
    message_count: int = 0
    total_bytes: int = 0
    unknown_session_fields: Tuple[str, ...] = ()
    unknown_message_fields: Tuple[str, ...] = ()
    errors: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PortabilityImportResult:
    """Bounded result for the explicitly enabled local import gate."""

    ok: bool
    status: str
    imported_ids: Tuple[str, ...] = ()
    skipped_ids: Tuple[str, ...] = ()
    detached_count: int = 0
    errors: Tuple[Mapping[str, Any], ...] = ()
    audit: Optional[PortabilityAudit] = None


def _as_session_list(payload: Any) -> Optional[List[Mapping[str, Any]]]:
    if isinstance(payload, Mapping):
        return [payload]
    if isinstance(payload, list) and all(isinstance(item, Mapping) for item in payload):
        return list(payload)
    return None


def audit_export_payload(
    payload: Any,
    *,
    max_sessions: int = IMPORT_MAX_SESSIONS,
    max_messages_per_session: int = IMPORT_MAX_MESSAGES_PER_SESSION,
    max_total_messages: int = IMPORT_MAX_TOTAL_MESSAGES,
    max_session_bytes: int = IMPORT_MAX_SESSION_BYTES,
    max_total_bytes: int = IMPORT_MAX_TOTAL_BYTES,
) -> PortabilityAudit:
    """Audit an ``export_session``/``export_all`` payload without mutation.

    The result reports every shape/size issue at the boundary. No file or
    SQLite connection is touched, and unknown fields are only reported so the
    current export shape remains unchanged.
    """
    sessions = _as_session_list(payload)
    if sessions is None:
        return PortabilityAudit(ok=False, errors=("payload must be a session object or list",))

    errors: List[str] = []
    unknown_sessions: set[str] = set()
    unknown_messages: set[str] = set()
    total_messages = 0
    total_bytes = 0

    if len(sessions) > max_sessions:
        errors.append(f"sessions must contain at most {max_sessions} entries")

    seen_ids: set[str] = set()
    for index, session in enumerate(sessions):
        session_id = str(session.get("id") or "").strip()
        if not session_id:
            errors.append(f"sessions[{index}].id is required")
        elif len(session_id) > MAX_SESSION_ID_CHARS:
            errors.append(
                f"sessions[{index}].id exceeds {MAX_SESSION_ID_CHARS} characters"
            )
        elif session_id in seen_ids:
            errors.append(f"sessions[{index}] duplicates session id")
        else:
            seen_ids.add(session_id)

        unknown_sessions.update(str(key) for key in session.keys() if key not in SESSION_EXPORT_FIELDS)
        messages = session.get("messages") or []
        if not isinstance(messages, list):
            errors.append(f"sessions[{index}].messages must be a list")
            continue
        if len(messages) > max_messages_per_session:
            errors.append(
                f"sessions[{index}].messages exceeds {max_messages_per_session} entries"
            )
        for message_index, message in enumerate(messages):
            if not isinstance(message, Mapping):
                errors.append(f"sessions[{index}].messages[{message_index}] must be an object")
                continue
            unknown_messages.update(
                str(key) for key in message.keys() if key not in MESSAGE_EXPORT_FIELDS
            )
            role = message.get("role")
            if not isinstance(role, str) or not role.strip():
                errors.append(f"sessions[{index}].messages[{message_index}].role is required")
        total_messages += len(messages)

        try:
            encoded = json.dumps(session, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError):
            errors.append(f"sessions[{index}] must be JSON serializable")
            continue
        session_bytes = len(encoded)
        total_bytes += session_bytes
        if session_bytes > max_session_bytes:
            errors.append(f"sessions[{index}] exceeds {max_session_bytes} bytes")

        segments = session.get("segments")
        lineage_ids = session.get("lineage_session_ids")
        if segments is not None or lineage_ids is not None:
            if not isinstance(segments, list) or not isinstance(lineage_ids, list):
                errors.append(f"sessions[{index}] lineage fields must both be lists")
            elif len(segments) != len(lineage_ids):
                errors.append(f"sessions[{index}] lineage segment/id counts differ")
            elif len({str(value) for value in lineage_ids}) != len(lineage_ids):
                errors.append(f"sessions[{index}] lineage ids must be unique")

    if total_messages > max_total_messages:
        errors.append(f"messages exceed {max_total_messages} total entries")
    if total_bytes > max_total_bytes:
        errors.append(f"payload exceeds {max_total_bytes} total bytes")

    return PortabilityAudit(
        ok=not errors,
        session_count=len(sessions),
        message_count=total_messages,
        total_bytes=total_bytes,
        unknown_session_fields=tuple(sorted(unknown_sessions)),
        unknown_message_fields=tuple(sorted(unknown_messages)),
        errors=tuple(errors),
    )


def _bounded_text(value: Any, *, limit: int = 500_000) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("text fields must be strings")
    return value[:limit]


def _finite_float(value: Any, *, default: Optional[float] = None) -> Optional[float]:
    if value is None or value == "":
        return default
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("numeric field must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError("numeric field must be a finite number")
    return result


def _bounded_int(value: Any, *, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("integer field must be an integer") from exc


def _json_text(value: Any, *, field: str) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field} must be valid JSON") from exc
    else:
        parsed = value
    try:
        return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be JSON serializable") from exc


def _normalize_import_payload(payload: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Project a validated export into the local v11 write shape.

    This function only prepares in-memory dictionaries. It deliberately does
    not import ``hermes_state`` or execute SQL, so callers can validate the
    complete batch before opening a write transaction.
    """
    sessions = _as_session_list(payload)
    if sessions is None:
        raise ValueError("payload must be a session object or list")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(sessions):
        session_id = str(raw.get("id") or "").strip()
        if not session_id:
            raise ValueError(f"sessions[{index}].id is required")
        if len(session_id) > MAX_SESSION_ID_CHARS:
            raise ValueError(
                f"sessions[{index}].id exceeds {MAX_SESSION_ID_CHARS} characters"
            )
        if session_id in seen_ids:
            raise ValueError(f"sessions[{index}] duplicates session id")
        seen_ids.add(session_id)

        messages = raw.get("messages") or []
        if not isinstance(messages, list):
            raise ValueError(f"sessions[{index}].messages must be a list")

        session: dict[str, Any] = {
            "id": session_id,
            "source": _bounded_text(raw.get("source")) or "import",
            "user_id": _bounded_text(raw.get("user_id"), limit=240),
            "model": _bounded_text(raw.get("model"), limit=500),
            "model_config": _json_text(raw.get("model_config"), field="model_config"),
            "system_prompt": _bounded_text(raw.get("system_prompt")),
            "parent_session_id": _bounded_text(raw.get("parent_session_id"), limit=240),
            "started_at": _finite_float(raw.get("started_at"), default=time.time()),
            "ended_at": _finite_float(raw.get("ended_at")),
            "end_reason": _bounded_text(raw.get("end_reason"), limit=240),
            "input_tokens": _bounded_int(raw.get("input_tokens")),
            "output_tokens": _bounded_int(raw.get("output_tokens")),
            "cache_read_tokens": _bounded_int(raw.get("cache_read_tokens")),
            "cache_write_tokens": _bounded_int(raw.get("cache_write_tokens")),
            "reasoning_tokens": _bounded_int(raw.get("reasoning_tokens")),
            "billing_provider": _bounded_text(raw.get("billing_provider"), limit=240),
            "billing_base_url": _bounded_text(raw.get("billing_base_url"), limit=2_048),
            "billing_mode": _bounded_text(raw.get("billing_mode"), limit=80),
            "estimated_cost_usd": _finite_float(raw.get("estimated_cost_usd")),
            "actual_cost_usd": _finite_float(raw.get("actual_cost_usd")),
            "cost_status": _bounded_text(raw.get("cost_status"), limit=80),
            "cost_source": _bounded_text(raw.get("cost_source"), limit=160),
            "pricing_version": _bounded_text(raw.get("pricing_version"), limit=160),
            "title": _bounded_text(raw.get("title"), limit=100),
            "api_call_count": _bounded_int(raw.get("api_call_count")),
        }

        clean_messages: list[dict[str, Any]] = []
        for message_index, raw_message in enumerate(messages):
            if not isinstance(raw_message, Mapping):
                raise ValueError(
                    f"sessions[{index}].messages[{message_index}] must be an object"
                )
            role = raw_message.get("role")
            if not isinstance(role, str) or not role.strip():
                raise ValueError(
                    f"sessions[{index}].messages[{message_index}].role is required"
                )
            clean_messages.append(
                {
                    "role": role.strip()[:80],
                    "content": raw_message.get("content"),
                    "tool_call_id": _bounded_text(raw_message.get("tool_call_id"), limit=240),
                    "tool_calls": _json_text(raw_message.get("tool_calls"), field="tool_calls"),
                    "tool_name": _bounded_text(raw_message.get("tool_name"), limit=240),
                    "timestamp": _finite_float(raw_message.get("timestamp")),
                    "token_count": (
                        _bounded_int(raw_message.get("token_count"))
                        if raw_message.get("token_count") not in (None, "")
                        else None
                    ),
                    "finish_reason": _bounded_text(raw_message.get("finish_reason"), limit=160),
                    "reasoning": _bounded_text(raw_message.get("reasoning")),
                    "reasoning_content": _bounded_text(raw_message.get("reasoning_content")),
                    "reasoning_details": _json_text(
                        raw_message.get("reasoning_details"), field="reasoning_details"
                    ),
                    "codex_reasoning_items": _json_text(
                        raw_message.get("codex_reasoning_items"),
                        field="codex_reasoning_items",
                    ),
                    "codex_message_items": _json_text(
                        raw_message.get("codex_message_items"),
                        field="codex_message_items",
                    ),
                }
            )
        normalized.append({"session": session, "messages": clean_messages})
    return normalized, []


def import_sessions_into_db(
    db: Any,
    payload: Any,
    *,
    enable: bool = False,
    dry_run: bool = True,
) -> PortabilityImportResult:
    """Import an export into a local v11 ``SessionDB`` only when explicitly enabled.

    The default is a no-write dry run.  Enabled imports project only columns
    present in the local v11 schema, execute one ``_execute_write`` transaction,
    detach missing/cyclic parent edges, and let the host transaction helper
    roll back the entire batch on any unexpected failure.
    """
    audit = audit_export_payload(payload)
    if not audit.ok:
        return PortabilityImportResult(
            ok=False,
            status="rejected",
            errors=tuple({"error": message} for message in audit.errors),
            audit=audit,
        )
    if not enable:
        return PortabilityImportResult(
            ok=False,
            status="disabled",
            errors=(
                {"error": "portability import is disabled; pass enable=True on a copied database"},
            ),
            audit=audit,
        )

    try:
        normalized, _ = _normalize_import_payload(payload)
    except ValueError as exc:
        return PortabilityImportResult(
            ok=False,
            status="rejected",
            errors=({"error": str(exc)},),
            audit=audit,
        )

    would_import = tuple(item["session"]["id"] for item in normalized)
    if dry_run:
        return PortabilityImportResult(
            ok=True,
            status="dry_run",
            imported_ids=would_import,
            audit=audit,
        )

    imported: list[str] = []
    skipped: list[str] = []
    parent_updates: list[tuple[str, str]] = []

    def _write(conn: Any) -> dict[str, Any]:
        for item in normalized:
            session = item["session"]
            session_id = session["id"]
            if conn.execute("SELECT 1 FROM sessions WHERE id = ? LIMIT 1", (session_id,)).fetchone():
                skipped.append(session_id)
                continue
            conn.execute(
                """INSERT INTO sessions (
                    id, source, user_id, model, model_config, system_prompt,
                    parent_session_id, started_at, ended_at, end_reason,
                    message_count, tool_call_count, input_tokens, output_tokens,
                    cache_read_tokens, cache_write_tokens, reasoning_tokens,
                    billing_provider, billing_base_url, billing_mode,
                    estimated_cost_usd, actual_cost_usd, cost_status, cost_source,
                    pricing_version, title, api_call_count
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session["id"], session["source"], session["user_id"], session["model"],
                    session["model_config"], session["system_prompt"], session["started_at"],
                    session["ended_at"], session["end_reason"], session["input_tokens"],
                    session["output_tokens"], session["cache_read_tokens"],
                    session["cache_write_tokens"], session["reasoning_tokens"],
                    session["billing_provider"], session["billing_base_url"],
                    session["billing_mode"], session["estimated_cost_usd"],
                    session["actual_cost_usd"], session["cost_status"], session["cost_source"],
                    session["pricing_version"], session["title"], session["api_call_count"],
                ),
            )
            message_count = 0
            tool_call_count = 0
            for message in item["messages"]:
                content = message["content"]
                encoder = getattr(db, "_encode_content", None)
                if callable(encoder):
                    content = encoder(content)
                conn.execute(
                    """INSERT INTO messages (
                        session_id, role, content, tool_call_id, tool_calls, tool_name,
                        timestamp, token_count, finish_reason, reasoning,
                        reasoning_content, reasoning_details, codex_reasoning_items,
                        codex_message_items
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        session_id, message["role"], content, message["tool_call_id"],
                        message["tool_calls"], message["tool_name"],
                        message["timestamp"] if message["timestamp"] is not None else time.time(),
                        message["token_count"], message["finish_reason"], message["reasoning"],
                        message["reasoning_content"], message["reasoning_details"],
                        message["codex_reasoning_items"], message["codex_message_items"],
                    ),
                )
                message_count += 1
                if message["tool_calls"]:
                    try:
                        tool_call_count += len(json.loads(message["tool_calls"]))
                    except (TypeError, ValueError):
                        tool_call_count += 1
            conn.execute(
                "UPDATE sessions SET message_count=?, tool_call_count=? WHERE id=?",
                (message_count, tool_call_count, session_id),
            )
            parent_id = session.get("parent_session_id")
            if parent_id:
                parent_updates.append((session_id, parent_id))
            imported.append(session_id)

        parent_by_child = dict(parent_updates)

        def _would_cycle(child: str, parent: str) -> bool:
            seen = {child}
            current = parent
            while current:
                if current in seen:
                    return True
                seen.add(current)
                if current in parent_by_child:
                    current = parent_by_child[current]
                    continue
                row = conn.execute(
                    "SELECT parent_session_id FROM sessions WHERE id=? LIMIT 1",
                    (current,),
                ).fetchone()
                current = row[0] if row else ""
            return False

        detached = 0
        for child, parent in parent_updates:
            exists = conn.execute("SELECT 1 FROM sessions WHERE id=? LIMIT 1", (parent,)).fetchone()
            if exists and not _would_cycle(child, parent):
                conn.execute("UPDATE sessions SET parent_session_id=? WHERE id=?", (parent, child))
            else:
                detached += 1
        return {"detached": detached}

    try:
        result = db._execute_write(_write)
    except Exception as exc:
        return PortabilityImportResult(
            ok=False,
            status="rolled_back",
            errors=({"error": str(exc)[:500]},),
            audit=audit,
        )
    return PortabilityImportResult(
        ok=True,
        status="imported",
        imported_ids=tuple(imported),
        skipped_ids=tuple(skipped),
        detached_count=int((result or {}).get("detached", 0)),
        audit=audit,
    )


__all__ = [
    "IMPORT_MAX_SESSIONS",
    "IMPORT_MAX_MESSAGES_PER_SESSION",
    "IMPORT_MAX_TOTAL_MESSAGES",
    "IMPORT_MAX_SESSION_BYTES",
    "IMPORT_MAX_TOTAL_BYTES",
    "MAX_SESSION_ID_CHARS",
    "SESSION_EXPORT_FIELDS",
    "MESSAGE_EXPORT_FIELDS",
    "PortabilityAudit",
    "PortabilityImportResult",
    "audit_export_payload",
    "import_sessions_into_db",
]
