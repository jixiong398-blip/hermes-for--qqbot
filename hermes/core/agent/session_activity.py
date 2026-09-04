"""Shared session activity observation contract.

Activity is an observation-only projection: timestamp plus bounded labels.
It does not decide when to notify, interrupt, retry, or expire a session.
"""

from __future__ import annotations

from enum import Enum
import math
import time
from typing import Any, Mapping, Optional


ACTIVITY_DESCRIPTION_MAX = 120

# Durable heartbeats are intentionally slower than the response-critical
# path. ``force_persist`` is reserved for explicit terminal/compression marks.
SESSION_ACTIVITY_HEARTBEAT_MIN_INTERVAL_SECONDS = 60.0


class ActivityProvenance(str, Enum):
    """Closed set of sources for a session activity observation."""

    UNKNOWN = "unknown"
    AGENT_COMPRESSION = "agent.compression"
    AGENT_COMPRESSION_TIMEOUT = "agent.compression_timeout"
    AGENT_COMPRESSION_COOLDOWN = "agent.compression_cooldown"


def bound_activity_description(description: Optional[str]) -> str:
    """Strip and clamp activity text to the shared description budget."""

    if description is None:
        text = ""
    elif isinstance(description, str):
        text = description.strip()
    else:
        try:
            text = str(description).strip()
        except Exception:
            text = ""
    if len(text) <= ACTIVITY_DESCRIPTION_MAX:
        return text
    return text[: ACTIVITY_DESCRIPTION_MAX - 3] + "..."


def normalize_activity_provenance(
    provenance: Optional[ActivityProvenance | str],
) -> ActivityProvenance:
    """Return a known provenance, or ``UNKNOWN`` for unrecognized input."""

    if isinstance(provenance, ActivityProvenance):
        return provenance
    if not isinstance(provenance, str):
        return ActivityProvenance.UNKNOWN
    try:
        return ActivityProvenance(provenance.strip())
    except ValueError:
        return ActivityProvenance.UNKNOWN


def reset_session_activity_persist_window(agent: Any) -> None:
    """Allow the next explicitly wired durable activity mark immediately."""

    try:
        agent._session_activity_last_persist_mono = 0.0
    except Exception:
        pass


def _safe_timestamp(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return timestamp if math.isfinite(timestamp) else None


def build_activity_snapshot(
    *,
    last_activity_at: Optional[float],
    last_activity_description: Optional[str],
    last_activity_provenance: Optional[ActivityProvenance | str] = None,
    now: Optional[float] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Build a bounded activity snapshot with legacy aliases."""

    when = _safe_timestamp(last_activity_at)
    clock = _safe_timestamp(now)
    if clock is None:
        clock = time.time()
    description = bound_activity_description(last_activity_description)
    provenance = normalize_activity_provenance(last_activity_provenance).value
    elapsed = None if when is None else round(max(0.0, clock - when), 1)
    snapshot: dict[str, Any] = {
        "last_activity_at": when,
        "last_activity_description": description,
        "last_activity_provenance": provenance,
        "seconds_since_activity": elapsed,
        "last_activity_ts": when,
        "last_activity_desc": description,
        "description": description,
        "provenance": provenance,
    }
    if extra:
        # Extras are diagnostic only; keep their shape bounded and detached.
        for key, value in list(extra.items())[:16]:
            key_text = str(key)[:80]
            if isinstance(value, str):
                snapshot[key_text] = value[:240]
            elif isinstance(value, (int, float, bool)) or value is None:
                snapshot[key_text] = value
    return snapshot


__all__ = [
    "ACTIVITY_DESCRIPTION_MAX",
    "ActivityProvenance",
    "SESSION_ACTIVITY_HEARTBEAT_MIN_INTERVAL_SECONDS",
    "bound_activity_description",
    "build_activity_snapshot",
    "normalize_activity_provenance",
    "reset_session_activity_persist_window",
]
