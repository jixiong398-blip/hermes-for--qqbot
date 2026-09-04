"""Offline tests for the durable delivery-obligation ledger."""

import json

import pytest

from gateway import delivery_ledger as ledger


@pytest.fixture()
def isolated_ledger(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    monkeypatch.setattr(ledger, "_db_path", lambda: db_path)
    monkeypatch.setattr(ledger, "_owner_stamp", lambda: (200, 300))
    monkeypatch.setattr(ledger, "_owner_alive", lambda pid, started: False)
    return db_path


def _record(oid="ob-1", *, platform="onebot", profile="default", content="reply"):
    ledger.record_obligation(
        obligation_id=oid,
        session_key="onebot:group:42",
        platform=platform,
        chat_id="group:42",
        thread_id=None,
        content=content,
        adapter_profile=profile,
    )


def _row(oid="ob-1"):
    with ledger._transaction() as conn:
        return conn.execute(
            "SELECT * FROM delivery_obligations WHERE obligation_id=?", (oid,)
        ).fetchone()


def test_lifecycle_records_pending_then_terminal_state(isolated_ledger):
    _record()
    assert _row()["state"] == "pending"
    assert _row()["attempts"] == 0

    ledger.mark_attempting("ob-1")
    assert _row()["state"] == "attempting"
    ledger.mark_delivered("ob-1")
    assert _row()["state"] == "delivered"

    diagnostic = json.loads(ledger.debug_rows())
    assert diagnostic[0]["id"] == "ob-1"
    assert diagnostic[0]["state"] == "delivered"


def test_record_is_idempotent_for_same_obligation_id(isolated_ledger):
    _record(content="first")
    ledger.mark_attempting("ob-1")
    _record(content="second")

    row = _row()
    assert row["state"] == "pending"
    assert row["attempts"] == 0
    assert row["content"] == "second"


def test_duplicate_record_after_delivery_does_not_resurrect_obligation(isolated_ledger):
    _record(content="first")
    ledger.mark_delivered("ob-1")

    _record(content="duplicate producer call")

    row = _row()
    assert row["state"] == "delivered"
    assert row["content"] == "first"


def test_sweep_claims_dead_owner_and_marks_ambiguous_attempting_rows(isolated_ledger):
    _record("pending")
    _record("attempting")
    ledger.mark_attempting("attempting")

    claimed = ledger.sweep_recoverable(now=1_000)

    assert {item["obligation_id"] for item in claimed} == {"pending", "attempting"}
    by_id = {item["obligation_id"]: item for item in claimed}
    assert by_id["pending"]["needs_marker"] is False
    assert by_id["attempting"]["needs_marker"] is True
    assert by_id["attempting"]["marker"] == ledger.RECOVERED_MARKER
    assert _row("pending")["owner_pid"] == 200
    assert _row("attempting")["attempts"] == 1


def test_sweep_respects_platform_and_profile_filters(isolated_ledger):
    _record("onebot", platform="onebot", profile="bot-a")
    _record("telegram", platform="telegram", profile="default")

    claimed = ledger.sweep_recoverable(
        now=1_000,
        deliverable_platforms={"onebot"},
        deliverable_targets={("onebot", "bot-a")},
    )

    assert [item["obligation_id"] for item in claimed] == ["onebot"]
    assert _row("telegram")["attempts"] == 0


def test_sweep_abandons_stale_or_exhausted_rows(isolated_ledger, monkeypatch):
    _record("stale")
    with ledger._transaction() as conn:
        conn.execute(
            "UPDATE delivery_obligations SET created_at=?, attempts=? WHERE obligation_id=?",
            (0, ledger.MAX_ATTEMPTS, "stale"),
        )

    assert ledger.sweep_recoverable(now=ledger.STALE_AFTER_SECONDS + 1) == []
    assert _row("stale")["state"] == "abandoned"


def test_runtime_sweep_claims_only_own_retryable_failure(isolated_ledger):
    _record("retry")
    ledger.mark_attempting("retry")
    ledger.mark_failed("retry", "send_path_degraded")
    _record("permanent", platform="onebot")
    ledger.mark_failed("permanent", "forbidden")

    claimed = ledger.sweep_failed_for_runtime("onebot", now=1_000)

    assert [item["obligation_id"] for item in claimed] == ["retry"]
    assert claimed[0]["runtime_recovery"] is True
    assert claimed[0]["marker"] == ledger.RECONNECTED_MARKER
    assert _row("retry")["state"] == "attempting"
    assert _row("permanent")["state"] == "failed"


def test_runtime_claim_release_is_owner_checked(isolated_ledger):
    _record("retry")
    ledger.mark_attempting("retry")

    assert ledger.release_runtime_claim("retry", "not sent") is True
    assert _row("retry")["state"] == "failed"
    assert _row("retry")["attempts"] == 0


def test_prune_removes_old_terminal_rows_and_bounds_diagnostics(isolated_ledger, monkeypatch):
    _record("old")
    ledger.mark_delivered("old")
    with ledger._transaction() as conn:
        conn.execute("UPDATE delivery_obligations SET updated_at=0 WHERE obligation_id='old'")
    ledger._prune(now=ledger.RETENTION_SECONDS + 1)
    assert _row("old") is None

    for index in range(5):
        _record(f"row-{index}")
    monkeypatch.setattr(ledger, "MAX_ROWS", 2)
    ledger._prune(now=1_000)
    assert len(json.loads(ledger.debug_rows(limit=100))) <= 2


def test_ledger_enabled_defaults_on_and_accepts_explicit_off():
    assert ledger.ledger_enabled({"gateway": {}}) is True
    assert ledger.ledger_enabled({"gateway": {"delivery_ledger": False}}) is False
    assert ledger.ledger_enabled({"gateway": {"delivery_ledger": "off"}}) is False
