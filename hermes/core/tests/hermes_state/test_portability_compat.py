"""Gate 4 read-only portability audit tests."""

from hermes_state import SessionDB
from hermes_state_portability_compat import (
    IMPORT_MAX_SESSION_BYTES,
    PortabilityAudit,
    audit_export_payload,
)


def _session(session_id="s1", messages=None):
    return {
        "id": session_id,
        "source": "cli",
        "model": "test-model",
        "started_at": 1.0,
        "messages": messages or [{"id": 1, "role": "user", "content": "hello"}],
    }


def test_valid_single_export_is_a_read_only_ready_audit():
    payload = _session()
    audit = audit_export_payload(payload)

    assert isinstance(audit, PortabilityAudit)
    assert audit.ok is True
    assert audit.session_count == 1
    assert audit.message_count == 1
    assert not audit.errors
    assert payload["messages"][0]["content"] == "hello"


def test_unknown_fields_are_reported_without_mutating_export():
    payload = _session()
    payload["future_runtime_field"] = "ignore me"
    payload["messages"][0]["future_message_field"] = {"nested": True}

    audit = audit_export_payload(payload)

    assert audit.ok is True
    assert audit.unknown_session_fields == ("future_runtime_field",)
    assert audit.unknown_message_fields == ("future_message_field",)
    assert payload["future_runtime_field"] == "ignore me"


def test_size_and_count_limits_fail_closed_before_any_write():
    payload = _session(messages=[{"role": "user", "content": "x" * IMPORT_MAX_SESSION_BYTES}])

    audit = audit_export_payload(payload)

    assert audit.ok is False
    assert any("exceeds" in error for error in audit.errors)


def test_lineage_shape_and_duplicate_ids_are_validated():
    payload = _session("tip")
    payload["lineage_session_ids"] = ["root", "root"]
    payload["segments"] = [{"id": "root"}, {"id": "tip"}]

    audit = audit_export_payload(payload)

    assert audit.ok is False
    assert any("lineage ids must be unique" in error for error in audit.errors)


def test_multiple_exports_accumulate_total_counts_and_reject_bad_roles():
    payload = [
        _session("s1"),
        _session("s2", messages=[{"role": "", "content": "bad"}]),
    ]

    audit = audit_export_payload(payload)

    assert audit.ok is False
    assert audit.session_count == 2
    assert audit.message_count == 2
    assert any("role is required" in error for error in audit.errors)


def test_invalid_payload_shape_does_not_touch_filesystem():
    audit = audit_export_payload("not-an-export")
    assert audit.ok is False
    assert audit.errors == ("payload must be a session object or list",)


def test_sessiondb_facade_audits_its_own_export_without_mutation(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("s1", source="cli")
        db.append_message("s1", "user", "portable evidence")
        before = db.get_session("s1")["message_count"]

        audit = db.audit_export_payload(db.export_session("s1"))

        assert audit.ok is True
        assert audit.message_count == 1
        assert db.get_session("s1")["message_count"] == before
    finally:
        db.close()
