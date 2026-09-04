"""Contract tests for the isolated session-stall policy."""

from __future__ import annotations

import math

import pytest

from gateway.session_stall import (
    format_session_stall_notification,
    resolve_session_idle_seconds_from_activity,
    should_clear_session_stall_notification,
    should_emit_session_stall_notification,
)


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({"timeout_seconds": 30, "idle_seconds": 30, "has_pending_inbound": True, "already_notified": False}, True),
        ({"timeout_seconds": 30, "idle_seconds": 29.9, "has_pending_inbound": True, "already_notified": False}, False),
        ({"timeout_seconds": 0, "idle_seconds": 100, "has_pending_inbound": True, "already_notified": False}, False),
        ({"timeout_seconds": 30, "idle_seconds": None, "has_pending_inbound": True, "already_notified": False}, False),
        ({"timeout_seconds": 30, "idle_seconds": 100, "has_pending_inbound": False, "already_notified": False}, False),
        ({"timeout_seconds": 30, "idle_seconds": 100, "has_pending_inbound": True, "already_notified": True}, False),
    ],
)
def test_emit_policy_is_bounded(kwargs, expected):
    assert should_emit_session_stall_notification(**kwargs) is expected


def test_clear_policy_holds_unknown_activity_latch():
    assert should_clear_session_stall_notification(
        timeout_seconds=30, idle_seconds=None, has_pending_inbound=True
    ) is False
    assert should_clear_session_stall_notification(
        timeout_seconds=30, idle_seconds=29, has_pending_inbound=True
    ) is True
    assert should_clear_session_stall_notification(
        timeout_seconds=30, idle_seconds=30, has_pending_inbound=True
    ) is False
    assert should_clear_session_stall_notification(
        timeout_seconds=30, idle_seconds=None, has_pending_inbound=False
    ) is True


def test_activity_resolution_prefers_finite_elapsed_and_clamps_negative():
    assert resolve_session_idle_seconds_from_activity(
        {"seconds_since_activity": 12}, now=100
    ) == 12
    assert resolve_session_idle_seconds_from_activity(
        {"seconds_since_activity": -2}, now=100
    ) == 0
    assert resolve_session_idle_seconds_from_activity(
        {"seconds_since_activity": float("nan"), "last_activity_at": 90}, now=100
    ) == 10


@pytest.mark.parametrize(
    "activity",
    [None, {}, {"seconds_since_activity": "bad"}, {"last_activity_at": float("inf")}],
)
def test_activity_resolution_returns_unknown_for_unusable_snapshots(activity):
    assert resolve_session_idle_seconds_from_activity(activity, now=100) is None


def test_activity_resolution_uses_timestamp_alias_and_never_returns_negative():
    assert resolve_session_idle_seconds_from_activity(
        {"last_activity_ts": 120}, now=100
    ) == 0
    assert resolve_session_idle_seconds_from_activity(
        {"last_activity_at": 80}, now=100
    ) == 20
    assert resolve_session_idle_seconds_from_activity(
        {"last_activity_at": 80}, now=math.inf
    ) is None


def test_format_notification_has_minimum_one_minute_and_is_stable():
    assert "1 min ago" in format_session_stall_notification(0)
    assert "3 min ago" in format_session_stall_notification(180)
