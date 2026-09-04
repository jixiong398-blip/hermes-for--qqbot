"""Bounded compatibility port for agent-as-provider transcript projection.

The upstream runtime lets ACP/Codex-like providers return already-completed
tool rows on a response object.  The local fork owns the main conversation
loop and SessionDB, so this first port is intentionally pure: it validates and
appends a bounded projection to a caller-owned in-memory message list, but it
does not wire itself into the loop or persist anything.
"""

from __future__ import annotations

try:
    import hermes_bootstrap  # noqa: F401
except ModuleNotFoundError:
    hermes_bootstrap = None  # type: ignore[assignment]

import logging
import json
import math
import time
from typing import Any

logger = logging.getLogger(__name__)

MAX_PROJECTED_ROWS = 64
MAX_PROJECTED_MESSAGE_CHARS = 200_000
MAX_PROJECTED_TOOL_CALLS = 32
MAX_PROJECTED_ROW_BYTES = 512_000
MAX_PROVIDER_TOOL_ITERATIONS = 1_000
_ALLOWED_ROLES = {"assistant", "tool"}


def _bounded_text(value: Any) -> str | None:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > MAX_PROJECTED_MESSAGE_CHARS:
        return None
    if any(ord(char) < 32 and char not in "\n\r\t" for char in value):
        return None
    return value


def _normalize_projected_row(row: Any) -> dict[str, Any] | None:
    """Copy one projected row without mutating provider-owned structures."""

    if not isinstance(row, dict):
        return None
    role = row.get("role")
    if role not in _ALLOWED_ROLES:
        return None
    normalized = dict(row)
    content = _bounded_text(normalized.get("content"))
    if content is None:
        return None
    normalized["content"] = content

    if role == "assistant":
        tool_calls = normalized.get("tool_calls")
        if tool_calls is not None:
            if not isinstance(tool_calls, list) or len(tool_calls) > MAX_PROJECTED_TOOL_CALLS:
                return None
            normalized["tool_calls"] = [
                dict(call)
                for call in tool_calls
                if isinstance(call, dict)
            ]
            if len(normalized["tool_calls"]) != len(tool_calls):
                return None
    else:
        for key in ("tool_call_id", "name"):
            value = normalized.get(key)
            if value is not None and (
                not isinstance(value, str) or len(value) > 512
            ):
                return None

    try:
        encoded_size = len(
            json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
            .encode("utf-8")
        )
    except (TypeError, ValueError, OverflowError, UnicodeError):
        return None
    if encoded_size > MAX_PROJECTED_ROW_BYTES:
        return None

    timestamp = normalized.get("timestamp")
    if timestamp is None:
        normalized["timestamp"] = time.time()
    else:
        try:
            timestamp_value = float(timestamp)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(timestamp_value):
            return None
        normalized["timestamp"] = timestamp_value
    return normalized


def splice_provider_projection(
    agent: Any,
    response: Any,
    messages: list[dict[str, Any]],
) -> int:
    """Append a bounded provider projection and tick the skill-review counter.

    This helper is a no-op for ordinary OpenAI-compatible responses.  It does
    not import the agent loop, open SQLite, invoke tools, or write files.
    """

    if not isinstance(messages, list):
        return 0
    projected = getattr(response, "hermes_projected_messages", None)
    rows = projected[:MAX_PROJECTED_ROWS] if isinstance(projected, list) else []
    appended = 0
    for row in rows:
        normalized = _normalize_projected_row(row)
        if normalized is None:
            continue
        messages.append(normalized)
        appended += 1

    raw_iterations = getattr(response, "hermes_provider_tool_iterations", 0)
    try:
        iterations = int(raw_iterations or 0)
    except (TypeError, ValueError, OverflowError):
        iterations = 0
    iterations = max(0, min(MAX_PROVIDER_TOOL_ITERATIONS, iterations))
    if iterations > 0 and agent is not None:
        try:
            current = int(getattr(agent, "_iters_since_skill", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            current = 0
        agent._iters_since_skill = max(0, current) + iterations

    if appended:
        logger.debug(
            "spliced %d provider-projected transcript row(s) from %s",
            appended,
            getattr(agent, "provider", "?"),
        )
    return appended


__all__ = [
    "MAX_PROJECTED_MESSAGE_CHARS",
    "MAX_PROJECTED_ROWS",
    "MAX_PROJECTED_ROW_BYTES",
    "MAX_PROJECTED_TOOL_CALLS",
    "MAX_PROVIDER_TOOL_ITERATIONS",
    "splice_provider_projection",
]
