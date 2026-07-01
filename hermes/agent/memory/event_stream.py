"""
Layer 0 Event Stream — immutable append-only JSONL truth source.

Every fact, decision, and milestone extracted by the memory system
is written here as a timestamped event. This file is the single
source of truth for cross-era migration.

Format:
  {"evt_id":"evt_000001","ts":"2026-05-16T03:14:00Z","type":"fact",...}
  {"evt_id":"evt_000002","ts":"2026-05-16T03:14:05Z","type":"decision",...}

Event types:
  message    — raw conversation turn (source of all downstream facts)
  fact       — distilled fact from consolidation
  decision   — a choice/commitment made in conversation
  preference — explicit user preference expressed
  milestone  — notable event (session milestone, system event)

Migration: copy this file. All downstream indexes (SQLite, vectors)
can be rebuilt from this single source.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Event stream file path
_HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
_DEFAULT_STREAM_PATH = _HERMES_HOME / "data" / "layer0.jsonl"
_STREAM_LOCK = threading.Lock()
_EVENT_COUNTER: int = 0
_STREAM_PATH: Optional[Path] = None


def set_stream_path(path: Path) -> None:
    """Override the default event stream path (for testing)."""
    global _STREAM_PATH
    _STREAM_PATH = path


def _get_stream_path() -> Path:
    global _STREAM_PATH
    if _STREAM_PATH:
        return _STREAM_PATH
    return _DEFAULT_STREAM_PATH


def _next_event_id() -> str:
    global _EVENT_COUNTER
    _EVENT_COUNTER += 1
    return f"evt_{_EVENT_COUNTER:08d}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_event(
    event_type: str,
    data: Dict[str, Any],
    sources: Optional[list] = None,
    stream_path: Optional[Path] = None,
) -> str:
    """Append a single event to the Layer 0 stream.

    Args:
        event_type: One of "message", "fact", "decision", "preference", "milestone"
        data: Event payload (category, key, value, etc.)
        sources: Optional list of evt_ids this event was derived from
        stream_path: Optional custom stream path

    Returns:
        The assigned evt_id
    """
    evt_id = _next_event_id()
    event = {
        "evt_id": evt_id,
        "ts": _now_iso(),
        "type": event_type,
        "sources": sources or [],
        **data,
    }

    path = stream_path or _get_stream_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))

    with _STREAM_LOCK:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    return evt_id


def write_message(
    session_id: str,
    role: str,
    content: str,
    speaker_name: str = "",
    platform: str = "",
    chat_type: str = "dm",
    message_id: str = "",
) -> str:
    """Record a raw conversation turn in the event stream."""
    return write_event(
        event_type="message",
        data={
            "session_id": session_id,
            "role": role,
            "content": content[:2000],  # truncate to avoid huge lines
            "speaker_name": speaker_name,
            "platform": platform,
            "chat_type": chat_type,
            "message_id": message_id,
        },
    )


def write_fact(
    category: str,
    key: str,
    value: str,
    confidence: float = 0.5,
    session_id: str = "",
    sources: Optional[list] = None,
) -> str:
    """Record a distilled fact in the event stream."""
    return write_event(
        event_type="fact",
        data={
            "category": category,
            "key": key,
            "value": value,
            "confidence": round(confidence, 2),
            "session_id": session_id,
        },
        sources=sources,
    )


def write_decision(
    subject: str,
    detail: str = "",
    outcome: str = "",
    session_id: str = "",
    sources: Optional[list] = None,
) -> str:
    """Record a decision made in conversation."""
    return write_event(
        event_type="decision",
        data={
            "subject": subject,
            "detail": detail,
            "outcome": outcome,
            "session_id": session_id,
        },
        sources=sources,
    )


def write_preference(
    key: str,
    value: str,
    confidence: float = 0.5,
    session_id: str = "",
    sources: Optional[list] = None,
) -> str:
    """Record an explicit user preference."""
    return write_event(
        event_type="preference",
        data={
            "key": key,
            "value": value,
            "confidence": round(confidence, 2),
            "session_id": session_id,
        },
        sources=sources,
    )


def write_milestone(
    milestone_type: str,
    detail: str = "",
    session_id: str = "",
) -> str:
    """Record a notable milestone (session start, deploy, version bump, etc.)."""
    return write_event(
        event_type="milestone",
        data={
            "milestone_type": milestone_type,
            "detail": detail,
            "session_id": session_id,
        },
    )


def read_events(
    since_ts: Optional[str] = None,
    event_types: Optional[list] = None,
    limit: int = 100,
    stream_path: Optional[Path] = None,
) -> list:
    """Read recent events from the stream, optionally filtered.

    Args:
        since_ts: ISO timestamp — only return events after this
        event_types: Filter to specific event types
        limit: Max events to return
        stream_path: Optional custom stream path

    Returns:
        List of event dicts, newest first
    """
    path = stream_path or _get_stream_path()
    if not path.exists():
        return []

    events = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event_types and evt.get("type") not in event_types:
                continue
            if since_ts and evt.get("ts", "") <= since_ts:
                continue

            events.append(evt)

    # Return newest first
    events.reverse()
    return events[:limit]


def get_stream_stats(stream_path: Optional[Path] = None) -> Dict[str, Any]:
    """Get basic stats about the event stream."""
    path = stream_path or _get_stream_path()
    if not path.exists():
        return {"exists": False, "total_events": 0, "size_bytes": 0}

    stats = {"exists": True, "size_bytes": path.stat().st_size}

    type_counts = {}
    first_ts = None
    last_ts = None
    total = 0

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            t = evt.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1
            ts = evt.get("ts", "")
            if ts:
                if not first_ts or ts < first_ts:
                    first_ts = ts
                if not last_ts or ts > last_ts:
                    last_ts = ts

    stats["total_events"] = total
    stats["type_counts"] = type_counts
    stats["first_ts"] = first_ts
    stats["last_ts"] = last_ts
    return stats
