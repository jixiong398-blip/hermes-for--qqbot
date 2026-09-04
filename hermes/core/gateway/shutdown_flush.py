"""Durable shutdown spool for gateway messages and live agent transcripts.

The gateway owns two in-memory queues that are cleared during shutdown:
platform adapter pending events and runner-level pending text.  A database or
adapter failure must not turn those queues into silent data loss.  This module
provides a small, dependency-light spool under ``HERMES_HOME`` and replay
helpers that are safe to call during startup and teardown on Windows or POSIX.

The spool is deliberately separate from SQLite.  Files are published with a
temporary file + fsync + replace sequence and are removed only after a replay
operation succeeds.  Malformed or unresolved payloads remain on disk for
manual recovery instead of being discarded.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

logger = logging.getLogger(__name__)

_SPOOL_GLOB = "pending-*.json"
_HISTORY_REASON = "shutdown-with-unpersisted-agent-history"
_DEFAULT_RECOVERY_TIME_BUDGET = 5.0


def _is_link_like(path: Path) -> bool:
    """Detect symlinks and Windows junctions without following them."""
    try:
        if path.is_symlink():
            return True
    except OSError:
        return True
    try:
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
    except OSError:
        return True
    return False


def _ensure_flush_dir(path: Path) -> Path:
    """Create and validate the spool directory without following symlinks."""
    if _is_link_like(path):
        raise OSError(f"shutdown spool directory is a symlink: {path}")
    path.mkdir(parents=True, exist_ok=True)
    # Check again after mkdir to cover a replacement race and reject regular
    # files, junctions, or other non-directory objects at this boundary.
    if _is_link_like(path):
        raise OSError(f"shutdown spool directory is a symlink: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"shutdown spool path is not a directory: {path}")
    if os.name == "posix":
        # A permissive existing directory must not silently become a shared
        # store for message content.  Refuse to continue if it cannot be
        # restricted rather than writing an insecure recovery file.
        os.chmod(path, 0o700)
    return path


def _get_flush_dir() -> Path:
    """Return the private shutdown spool directory for the active profile."""
    from hermes_constants import get_hermes_home

    return _ensure_flush_dir(get_hermes_home() / "pending_messages")


def _fsync_directory(path: Path) -> None:
    """Persist a directory entry where directory fsync is supported."""
    if os.name != "posix":
        return
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_payload(payload: Dict[str, Any], *, flush_dir: Optional[Path] = None) -> Path:
    """Atomically publish one owner-readable JSON payload."""
    directory = _ensure_flush_dir(flush_dir or _get_flush_dir())
    final_path = directory / f"pending-{uuid.uuid4().hex}.json"
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{final_path.stem}-",
        suffix=".tmp",
        dir=str(directory),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        # mkstemp is 0600 on supported platforms.  Keep the explicit chmod as
        # a defense for unusual umask/runtime implementations.
        try:
            os.chmod(temp_name, 0o600)
        except OSError:
            if os.name == "posix":
                raise
        os.replace(temp_name, final_path)
        try:
            _fsync_directory(directory)
        except OSError:
            logger.debug("Could not fsync shutdown spool directory: %s", directory)
        return final_path
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _json_value(value: Any) -> Any:
    """Return a JSON-safe value without invoking arbitrary repr machinery twice."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def serialise_pending_value(value: Any) -> Dict[str, Any]:
    """Convert a runner string, mapping, or ``MessageEvent`` to spool data."""
    if isinstance(value, str):
        return {"text": value}
    if isinstance(value, dict):
        converted = _json_value(value)
        return converted if isinstance(converted, dict) else {"text": str(converted)}

    # MessageEvent is intentionally duck-typed to avoid importing platform
    # modules during shutdown.  Preserve the source so a future operator can
    # resolve a missing session ID manually.
    result: Dict[str, Any] = {}
    try:
        result["text"] = str(getattr(value, "text", "") or "")
    except Exception:
        result["text"] = ""
    for attr in (
        "session_id",
        "message_id",
        "platform_update_id",
        "reply_to_message_id",
        "reply_to_text",
        "timestamp",
        "media_urls",
        "media_types",
        "channel_prompt",
    ):
        try:
            candidate = getattr(value, attr, None)
        except Exception:
            candidate = None
        if candidate is not None:
            result[attr] = _json_value(candidate)
    try:
        source = getattr(value, "source", None)
        if source is not None:
            source_dict = source.to_dict() if hasattr(source, "to_dict") else source
            result["source"] = _json_value(source_dict)
    except Exception:
        pass
    return result


# Backward-compatible spelling used by upstream tests and operators.
_serialise_value = serialise_pending_value


def flush_pending_to_file(
    pending: Dict[str, Any],
    *,
    reason: str = "shutdown",
    session_id_resolver: Optional[Callable[[str], Optional[str]]] = None,
) -> int:
    """Spool non-empty pending queue entries and return the number published."""
    if not isinstance(pending, dict) or not pending:
        return 0
    flushed = 0
    timestamp = int(time.time())
    try:
        pending_items = list(pending.items())
    except Exception as exc:
        logger.warning("Could not snapshot pending messages for shutdown: %s", exc)
        return 0
    for session_key, value in pending_items:
        if value is None:
            continue
        try:
            data = serialise_pending_value(value)
            if not data.get("text") and not data.get("source"):
                continue
            session_id = data.get("session_id")
            if not session_id and session_id_resolver is not None:
                try:
                    session_id = session_id_resolver(str(session_key))
                except Exception:
                    session_id = None
            if session_id:
                data["session_id"] = str(session_id)
            _write_payload(
                {
                    "session_key": str(session_key),
                    "reason": str(reason),
                    "ts": timestamp,
                    "data": data,
                }
            )
            flushed += 1
        except Exception as exc:
            logger.warning("Could not spool pending message %s: %s", session_key, exc)
    if flushed:
        logger.info("Spooled %d pending message(s) before %s", flushed, reason)
    return flushed


def flush_agent_history_to_file(session_id: Optional[str], history: Iterable[Any]) -> int:
    """Persist an unsaved agent transcript snapshot for manual replay."""
    try:
        messages = [_json_value(message) for message in list(history or [])]
    except Exception:
        messages = []
    if not messages:
        return 0
    try:
        _write_payload(
            {
                "reason": _HISTORY_REASON,
                "issue": "shutdown-transcript-preservation",
                "session_id": session_id,
                "count": len(messages),
                "messages": messages,
                "ts": int(time.time()),
            }
        )
        logger.warning(
            "Preserved %d in-memory message(s) for session %s",
            len(messages),
            session_id or "unknown",
        )
        return len(messages)
    except Exception as exc:
        logger.warning("Could not preserve agent history for %s: %s", session_id, exc)
        return 0


def _iter_payload_files(flush_dir: Path) -> list[Path]:
    try:
        return sorted(flush_dir.glob(_SPOOL_GLOB), key=lambda path: path.name)
    except Exception as exc:
        logger.warning("Could not scan shutdown spool: %s", exc)
        return []


def _is_regular_payload_file(path: Path) -> bool:
    """Return true only for a regular, non-symlink spool file."""
    try:
        if _is_link_like(path):
            return False
        return stat.S_ISREG(os.lstat(str(path)).st_mode)
    except (OSError, ValueError):
        return False


def _read_payload(path: Path) -> Any:
    """Read a regular payload without following a POSIX symlink race."""
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow:
        fd = os.open(str(path), os.O_RDONLY | nofollow)
        try:
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                fd = None
                return json.load(handle)
        finally:
            if fd is not None:
                os.close(fd)
    return json.loads(path.read_text(encoding="utf-8"))


def recover_pending_to_db(
    session_db: Any = None,
    *,
    time_budget: Optional[float] = _DEFAULT_RECOVERY_TIME_BUDGET,
) -> int:
    """Replay resolvable pending messages into ``SessionDB``.

    A payload is deleted only after ``append_message`` returns successfully.
    Agent-history snapshots and unresolved entries remain available for manual
    recovery because their exact transcript shape may require a higher-level
    session rewrite rather than a single user-message insert.
    """
    try:
        flush_dir = _ensure_flush_dir(_get_flush_dir())
    except Exception as exc:
        logger.warning("Could not prepare shutdown spool for recovery: %s", exc)
        return 0
    files = _iter_payload_files(flush_dir)
    if not files:
        return 0
    own_db = session_db is None
    if own_db:
        try:
            from hermes_state import SessionDB

            session_db = SessionDB()
        except Exception as exc:
            logger.warning("Could not open SessionDB for shutdown recovery: %s", exc)
            return 0

    recovered = 0
    deadline = None
    budget_seconds = None
    try:
        if time_budget is not None and float(time_budget) > 0:
            budget_seconds = float(time_budget)
            deadline = time.monotonic() + budget_seconds
    except (TypeError, ValueError):
        budget_seconds = _DEFAULT_RECOVERY_TIME_BUDGET
        deadline = time.monotonic() + budget_seconds
    try:
        for path in files:
            if deadline is not None and time.monotonic() >= deadline:
                logger.warning(
                    "Shutdown payload recovery reached its %.1fs time budget; "
                    "remaining files will be retried later",
                    budget_seconds,
                )
                break
            try:
                if not _is_regular_payload_file(path):
                    logger.warning("Skipping non-regular shutdown payload %s", path)
                    continue
                payload = _read_payload(path)
                if not isinstance(payload, dict):
                    continue
                if payload.get("reason") == _HISTORY_REASON:
                    continue
                data = payload.get("data")
                if not isinstance(data, dict):
                    continue
                session_id = str(
                    data.get("session_id") or payload.get("session_id") or ""
                ).strip()
                text = str(data.get("text") or "")
                if not session_id or not text:
                    logger.warning("Keeping unresolved shutdown payload %s", path)
                    continue
                append_result = session_db.append_message(
                    session_id=session_id,
                    role="user",
                    content=text,
                )
                if append_result is False:
                    raise RuntimeError("SessionDB rejected shutdown payload")
                path.unlink()
                recovered += 1
            except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
                logger.warning("Could not recover shutdown payload %s: %s", path, exc)
            except Exception as exc:
                # A database failure is likely transient; keep the payload for
                # the next startup attempt.
                logger.warning("Shutdown payload replay failed for %s: %s", path, exc)
    finally:
        if own_db and session_db is not None:
            try:
                session_db.close()
            except Exception:
                pass
    if recovered:
        logger.info("Recovered %d pending message(s) after startup", recovered)
    return recovered


__all__ = [
    "flush_pending_to_file",
    "flush_agent_history_to_file",
    "recover_pending_to_db",
    "serialise_pending_value",
    "_serialise_value",
]
