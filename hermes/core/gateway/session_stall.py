"""Bounded session-stall notification policy.

This is an isolated upstream-compatible policy port.  It consumes an activity
snapshot supplied by a caller and never invents a timestamp from turn start or
inbound queue state.  Gateway integration is intentionally deferred until the
local activity contract and OneBot notification path are reviewed together.
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import Any, Optional


def should_emit_session_stall_notification(
    *,
    timeout_seconds: float,
    idle_seconds: Optional[float],
    has_pending_inbound: bool,
    already_notified: bool,
) -> bool:
    """Return whether a one-shot stall warning should be emitted."""

    if timeout_seconds <= 0:
        return False
    if not has_pending_inbound or already_notified:
        return False
    if idle_seconds is None:
        return False
    return idle_seconds >= timeout_seconds


def should_clear_session_stall_notification(
    *,
    timeout_seconds: float,
    idle_seconds: Optional[float],
    has_pending_inbound: bool,
) -> bool:
    """Return whether a previous stall latch can be cleared."""

    if not has_pending_inbound or timeout_seconds <= 0:
        return True
    # Unknown activity is not evidence of recovery.
    if idle_seconds is None:
        return False
    return idle_seconds < timeout_seconds


def format_session_stall_notification(idle_seconds: float) -> str:
    """Format a bounded user-facing stall warning."""

    mins = max(1, int(idle_seconds // 60))
    return (
        f"Agent session appears stalled (last activity {mins} min ago). "
        "Try /new to reset."
    )


def resolve_session_idle_seconds_from_activity(
    activity: Optional[Mapping[str, Any]],
    *,
    now: Optional[float] = None,
) -> Optional[float]:
    """Resolve idle time from the shared activity snapshot only.

    ``seconds_since_activity`` wins when finite.  Otherwise the helper derives
    elapsed time from ``last_activity_at`` or ``last_activity_ts``.  Missing,
    malformed, future, and non-finite values never become a stall signal.
    """

    if not activity:
        return None

    elapsed = activity.get("seconds_since_activity")
    if elapsed is not None:
        try:
            idle = float(elapsed)
        except (TypeError, ValueError):
            idle = None
        else:
            if math.isfinite(idle):
                return max(0.0, idle)

    timestamp = activity.get("last_activity_at")
    if timestamp is None:
        timestamp = activity.get("last_activity_ts")
    if timestamp is None:
        return None
    try:
        when = float(timestamp)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(when):
        return None

    clock = time.time() if now is None else float(now)
    if not math.isfinite(clock):
        return None
    return max(0.0, clock - when)


__all__ = [
    "format_session_stall_notification",
    "resolve_session_idle_seconds_from_activity",
    "should_clear_session_stall_notification",
    "should_emit_session_stall_notification",
]
