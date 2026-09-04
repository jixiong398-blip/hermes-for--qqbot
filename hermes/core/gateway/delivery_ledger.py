"""Durable delivery-obligation ledger for gateway final responses.

The ledger records a response before a platform send begins and marks it only
after the adapter confirms success.  Crash/restart recovery is deliberately
at-least-once: an obligation that was in-flight receives a visible recovery
marker instead of being silently duplicated.  Ledger failures are best-effort
and must never prevent the normal send path from running.

This module is an isolated port.  Callers opt in by wrapping their final
delivery path; importing it alone does not create a database table.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from hermes_constants import get_state_db_path

logger = logging.getLogger(__name__)

_DB_LOCK = threading.RLock()
MAX_ATTEMPTS = 3
STALE_AFTER_SECONDS = 24 * 60 * 60
RETENTION_SECONDS = 7 * 24 * 60 * 60
MAX_ROWS = 500
MAX_CONTENT_CHARS = 500_000
MAX_ERROR_CHARS = 500

RECOVERED_MARKER = (
    "Recovered reply: the gateway restarted during delivery, "
    "so this may be a duplicate:\n\n"
)
RECONNECTED_MARKER = (
    "Recovered reply: the messaging platform reconnected after the original "
    "delivery failed, so this may be a duplicate:\n\n"
)
_RUNTIME_RETRYABLE_ERRORS = frozenset({"send_path_degraded"})


def _db_path() -> Any:
    return get_state_db_path()


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        from hermes_state import apply_wal_with_fallback

        apply_wal_with_fallback(conn, db_label="state.db (delivery_ledger)")
        conn.execute(
            """CREATE TABLE IF NOT EXISTS delivery_obligations (
                obligation_id TEXT PRIMARY KEY,
                session_key TEXT NOT NULL,
                platform TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                thread_id TEXT,
                content TEXT NOT NULL,
                state TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                owner_pid INTEGER,
                owner_started_at INTEGER,
                last_error TEXT,
                adapter_profile TEXT
            )"""
        )
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(delivery_obligations)")
        }
        if "adapter_profile" not in columns:
            conn.execute(
                "ALTER TABLE delivery_obligations ADD COLUMN adapter_profile TEXT"
            )
        conn.commit()
        return conn
    except Exception:
        conn.close()
        raise


@contextmanager
def _transaction() -> Iterator[sqlite3.Connection]:
    conn = _connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _owner_stamp() -> tuple[int, Optional[int]]:
    pid = os.getpid()
    try:
        from gateway.status import get_process_start_time

        return pid, get_process_start_time(pid)
    except Exception:
        return pid, None


def _owner_alive(pid: Any, started_at: Any) -> bool:
    """Return True only when the recorded process identity is still live."""
    if not pid:
        return False
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    try:
        from gateway.status import get_process_start_time, _pid_exists

        current_start = get_process_start_time(pid_int)
        if current_start is None:
            return bool(_pid_exists(pid_int))
        if started_at is None:
            return True
        return int(current_start) == int(started_at)
    except Exception:
        # Never let a liveness probe block recovery. On Windows a raw
        # os.kill(pid, 0) is not a safe fallback because it can signal a
        # console group, so an unreadable identity is treated as dead.
        if os.name == "nt":
            return False
        try:
            os.kill(pid_int, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False
        return True


def compute_obligation_id(session_key: str, message_ref: str, content: str) -> str:
    """Return a stable id for one session turn and response body."""
    payload = "|".join(
        (
            str(session_key or ""),
            str(message_ref or ""),
            str(content or "")[:MAX_CONTENT_CHARS],
        )
    )
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()[:24]


def record_obligation(
    *,
    obligation_id: str,
    session_key: str,
    platform: str,
    chat_id: str,
    thread_id: Optional[str],
    content: str,
    adapter_profile: Optional[str] = None,
) -> None:
    """Record a response before its platform send begins."""
    if not obligation_id or not session_key or not platform or not chat_id:
        return
    now = time.time()
    pid, started = _owner_stamp()
    with _DB_LOCK, _transaction() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO delivery_obligations
               (obligation_id, session_key, platform, chat_id, thread_id,
                content, state, attempts, created_at, updated_at,
                owner_pid, owner_started_at, adapter_profile)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?, ?)""",
            (
                str(obligation_id)[:120],
                str(session_key)[:500],
                str(platform)[:80],
                str(chat_id)[:500],
                str(thread_id)[:500] if thread_id else None,
                str(content or "")[:MAX_CONTENT_CHARS],
                now,
                now,
                pid,
                started,
                str(adapter_profile).strip()[:200] if adapter_profile else "default",
            ),
        )
        # A duplicate producer call may happen after the adapter already
        # confirmed delivery. Preserve that terminal state so a retry cannot
        # resurrect an obligation and cause a second recovery send.
        conn.execute(
            """UPDATE delivery_obligations
               SET session_key=?, platform=?, chat_id=?, thread_id=?, content=?,
                   state='pending', attempts=0, created_at=?, updated_at=?,
                   owner_pid=?, owner_started_at=?, adapter_profile=?
               WHERE obligation_id=? AND state <> 'delivered'""",
            (
                str(session_key)[:500],
                str(platform)[:80],
                str(chat_id)[:500],
                str(thread_id)[:500] if thread_id else None,
                str(content or "")[:MAX_CONTENT_CHARS],
                now,
                now,
                pid,
                started,
                str(adapter_profile).strip()[:200] if adapter_profile else "default",
                str(obligation_id)[:120],
            ),
        )
    _prune(now)


def _update_state(obligation_id: str, state: str, error: str = "") -> None:
    if not obligation_id:
        return
    with _DB_LOCK, _transaction() as conn:
        conn.execute(
            """UPDATE delivery_obligations
               SET state=?, updated_at=?, last_error=?
               WHERE obligation_id=?""",
            (state, time.time(), str(error or "")[:MAX_ERROR_CHARS] or None, obligation_id),
        )


def mark_attempting(obligation_id: str) -> None:
    _update_state(obligation_id, "attempting")


def mark_delivered(obligation_id: str) -> None:
    _update_state(obligation_id, "delivered")


def mark_failed(obligation_id: str, error: str = "") -> None:
    _update_state(obligation_id, "failed", error)


def release_runtime_claim(obligation_id: str, error: str = "") -> bool:
    """Return an unattempted runtime claim to failed without spending budget."""
    pid, started = _owner_stamp()
    if started is None:
        return False
    with _DB_LOCK, _transaction() as conn:
        cursor = conn.execute(
            """UPDATE delivery_obligations
               SET state='failed', attempts=CASE WHEN attempts > 0 THEN attempts - 1 ELSE 0 END,
                   updated_at=?, last_error=?
               WHERE obligation_id=? AND state='attempting'
                 AND owner_pid IS ? AND owner_started_at IS ?""",
            (
                time.time(),
                str(error or "")[:MAX_ERROR_CHARS] or None,
                obligation_id,
                pid,
                started,
            ),
        )
    return bool(cursor.rowcount)


def sweep_recoverable(
    now: Optional[float] = None,
    *,
    deliverable_platforms: Optional[set] = None,
    deliverable_targets: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """Claim pending/attempting/failed rows whose owner is dead."""
    now = time.time() if now is None else float(now)
    pid, started = _owner_stamp()
    claimed: List[Dict[str, Any]] = []
    with _DB_LOCK, _transaction() as conn:
        rows = conn.execute(
            """SELECT obligation_id, session_key, platform, chat_id, thread_id,
                      content, state, attempts, created_at, owner_pid,
                      owner_started_at, adapter_profile
               FROM delivery_obligations
               WHERE state IN ('pending', 'attempting', 'failed')"""
        ).fetchall()
        for row in rows:
            if _owner_alive(row["owner_pid"], row["owner_started_at"]):
                continue
            if row["attempts"] >= MAX_ATTEMPTS or now - row["created_at"] > STALE_AFTER_SECONDS:
                conn.execute(
                    "UPDATE delivery_obligations SET state='abandoned', updated_at=? WHERE obligation_id=?",
                    (now, row["obligation_id"]),
                )
                continue
            if deliverable_platforms is not None and row["platform"] not in deliverable_platforms:
                continue
            if deliverable_targets is not None and (
                row["platform"], row["adapter_profile"] or "default"
            ) not in deliverable_targets:
                continue
            cursor = conn.execute(
                """UPDATE delivery_obligations
                   SET owner_pid=?, owner_started_at=?, attempts=attempts+1, updated_at=?
                   WHERE obligation_id=? AND (owner_pid IS ? OR owner_pid=?)""",
                (
                    pid,
                    started,
                    now,
                    row["obligation_id"],
                    row["owner_pid"],
                    row["owner_pid"],
                ),
            )
            if cursor.rowcount:
                claimed.append(
                    {
                        "obligation_id": row["obligation_id"],
                        "session_key": row["session_key"],
                        "platform": row["platform"],
                        "chat_id": row["chat_id"],
                        "thread_id": row["thread_id"],
                        "content": row["content"],
                        "needs_marker": row["state"] != "pending",
                        "marker": RECOVERED_MARKER if row["state"] != "pending" else "",
                        "profile": row["adapter_profile"] or "default",
                        "attempts": int(row["attempts"] or 0) + 1,
                    }
                )
    return claimed


def sweep_failed_for_runtime(
    platform: str,
    now: Optional[float] = None,
    *,
    profile: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Claim this process's explicitly retryable failures after reconnect."""
    now = time.time() if now is None else float(now)
    pid, started = _owner_stamp()
    if started is None:
        return []
    expected_profile = "default" if not profile or profile == "default" else str(profile)
    claimed: List[Dict[str, Any]] = []
    with _DB_LOCK, _transaction() as conn:
        rows = conn.execute(
            """SELECT obligation_id, session_key, platform, chat_id, thread_id,
                      content, attempts, created_at, owner_pid, owner_started_at,
                      last_error, adapter_profile
               FROM delivery_obligations WHERE state='failed' AND platform=?""",
            (str(platform),),
        ).fetchall()
        for row in rows:
            if (row["adapter_profile"] or "default") != expected_profile:
                continue
            if row["owner_pid"] != pid or row["owner_started_at"] != started:
                continue
            if str(row["last_error"] or "").strip().lower() not in _RUNTIME_RETRYABLE_ERRORS:
                continue
            if row["attempts"] >= MAX_ATTEMPTS or now - row["created_at"] > STALE_AFTER_SECONDS:
                conn.execute(
                    """UPDATE delivery_obligations SET state='abandoned', updated_at=?
                       WHERE obligation_id=? AND state='failed'
                         AND owner_pid IS ? AND owner_started_at IS ?""",
                    (now, row["obligation_id"], pid, started),
                )
                continue
            cursor = conn.execute(
                """UPDATE delivery_obligations SET state='attempting', attempts=attempts+1, updated_at=?
                   WHERE obligation_id=? AND state='failed'
                     AND owner_pid IS ? AND owner_started_at IS ?""",
                (now, row["obligation_id"], pid, started),
            )
            if cursor.rowcount:
                claimed.append(
                    {
                        "obligation_id": row["obligation_id"],
                        "session_key": row["session_key"],
                        "platform": row["platform"],
                        "chat_id": row["chat_id"],
                        "thread_id": row["thread_id"],
                        "content": row["content"],
                        "needs_marker": True,
                        "marker": RECONNECTED_MARKER,
                        "profile": row["adapter_profile"] or "default",
                        "runtime_recovery": True,
                        "attempts": int(row["attempts"] or 0) + 1,
                    }
                )
    return claimed


def _prune(now: Optional[float] = None) -> None:
    now = time.time() if now is None else float(now)
    try:
        with _DB_LOCK, _transaction() as conn:
            conn.execute(
                "DELETE FROM delivery_obligations WHERE state IN ('delivered','abandoned') AND updated_at < ?",
                (now - RETENTION_SECONDS,),
            )
            total = conn.execute("SELECT COUNT(*) FROM delivery_obligations").fetchone()[0]
            excess = max(0, int(total) - MAX_ROWS)
            if excess:
                conn.execute(
                    """DELETE FROM delivery_obligations WHERE obligation_id IN (
                         SELECT obligation_id FROM delivery_obligations
                         ORDER BY CASE state WHEN 'delivered' THEN 0 WHEN 'abandoned' THEN 1 ELSE 2 END,
                                  updated_at ASC LIMIT ?)""",
                    (excess,),
                )
    except Exception:
        logger.debug("delivery ledger prune failed", exc_info=True)


def ledger_enabled(config: Optional[Dict[str, Any]] = None) -> bool:
    """Return the gateway.delivery_ledger gate; default is enabled."""
    try:
        if config is None:
            from hermes_cli.config import load_config

            config = load_config() or {}
        value = (config.get("gateway") or {}).get("delivery_ledger", True)
        if isinstance(value, str):
            return value.strip().lower() not in {"false", "0", "no", "off"}
        return bool(value)
    except Exception:
        return True


def debug_rows(limit: int = 20) -> str:
    """Return a bounded JSON diagnostic view for local operator tooling."""
    try:
        safe_limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        safe_limit = 20
    with _DB_LOCK, _transaction() as conn:
        rows = conn.execute(
            """SELECT obligation_id, session_key, state, attempts,
                      created_at, updated_at, last_error
               FROM delivery_obligations ORDER BY updated_at DESC LIMIT ?""",
            (safe_limit,),
        ).fetchall()
    return json.dumps(
        [
            {
                "id": row["obligation_id"],
                "session": row["session_key"],
                "state": row["state"],
                "attempts": row["attempts"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "last_error": row["last_error"],
            }
            for row in rows
        ],
        ensure_ascii=False,
    )
