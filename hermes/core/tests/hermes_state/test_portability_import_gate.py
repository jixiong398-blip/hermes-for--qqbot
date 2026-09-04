"""Copy-only tests for the explicitly enabled SessionDB portability importer."""

from hermes_state import SessionDB
from hermes_state_portability_compat import MAX_SESSION_ID_CHARS, PortabilityImportResult


def _export_payload(source_db: SessionDB):
    source_db.create_session("root", source="onebot", model="model-a")
    source_db.append_message("root", "user", [{"type": "text", "text": "hello"}])
    source_db.append_message(
        "root",
        "assistant",
        "reply",
        reasoning_details={"kind": "summary"},
    )
    return source_db.export_all()


def test_default_import_is_disabled_and_does_not_write(tmp_path):
    source = SessionDB(tmp_path / "source.db")
    target = SessionDB(tmp_path / "target.db")
    try:
        payload = _export_payload(source)
        result = target.import_sessions(payload)

        assert isinstance(result, PortabilityImportResult)
        assert result.ok is False
        assert result.status == "disabled"
        assert target.session_count() == 0
    finally:
        source.close()
        target.close()


def test_dry_run_reports_would_import_without_writing(tmp_path):
    source = SessionDB(tmp_path / "source.db")
    target = SessionDB(tmp_path / "target.db")
    try:
        payload = _export_payload(source)
        result = target.import_sessions(payload, enable=True, dry_run=True)

        assert result.ok is True
        assert result.status == "dry_run"
        assert result.imported_ids == ("root",)
        assert target.session_count() == 0
    finally:
        source.close()
        target.close()


def test_enabled_import_projects_v11_fields_and_encodes_structured_content(tmp_path):
    source = SessionDB(tmp_path / "source.db")
    target = SessionDB(tmp_path / "target.db")
    try:
        payload = _export_payload(source)
        payload[0]["future_field"] = "ignored"
        result = target.import_sessions(payload, enable=True, dry_run=False)

        assert result.ok is True
        assert result.status == "imported"
        assert result.imported_ids == ("root",)
        assert target.session_count() == 1
        assert target.message_count("root") == 2
        messages = target.get_messages("root")
        assert messages[0]["content"] == [{"type": "text", "text": "hello"}]
        assert messages[1]["reasoning_details"] == '{"kind":"summary"}'
    finally:
        source.close()
        target.close()


def test_duplicate_import_is_idempotent_and_does_not_duplicate_messages(tmp_path):
    source = SessionDB(tmp_path / "source.db")
    target = SessionDB(tmp_path / "target.db")
    try:
        payload = _export_payload(source)
        first = target.import_sessions(payload, enable=True, dry_run=False)
        second = target.import_sessions(payload, enable=True, dry_run=False)

        assert first.imported_ids == ("root",)
        assert second.imported_ids == ()
        assert second.skipped_ids == ("root",)
        assert target.message_count("root") == 2
    finally:
        source.close()
        target.close()


def test_missing_or_cyclic_parent_edges_are_detached_without_rollback(tmp_path):
    target = SessionDB(tmp_path / "target.db")
    try:
        payload = [
            {
                "id": "child",
                "source": "import",
                "parent_session_id": "missing",
                "started_at": 1.0,
                "messages": [{"role": "user", "content": "child"}],
            },
            {
                "id": "a",
                "source": "import",
                "parent_session_id": "b",
                "started_at": 2.0,
                "messages": [{"role": "user", "content": "a"}],
            },
            {
                "id": "b",
                "source": "import",
                "parent_session_id": "a",
                "started_at": 3.0,
                "messages": [{"role": "user", "content": "b"}],
            },
        ]
        result = target.import_sessions(payload, enable=True, dry_run=False)

        assert result.ok is True
        assert result.detached_count == 3
        assert target.get_session("child")["parent_session_id"] is None
        assert target.get_session("a")["parent_session_id"] is None
        assert target.get_session("b")["parent_session_id"] is None
    finally:
        target.close()


def test_invalid_batch_is_rejected_before_any_write(tmp_path):
    target = SessionDB(tmp_path / "target.db")
    try:
        payload = [
            {
                "id": "good",
                "source": "import",
                "started_at": 1.0,
                "messages": [{"role": "user", "content": "ok"}],
            },
            {
                "id": "bad",
                "source": "import",
                "started_at": "not-a-number",
                "messages": [{"role": "user", "content": "bad"}],
            },
        ]
        result = target.import_sessions(payload, enable=True, dry_run=False)

        assert result.ok is False
        assert result.status == "rejected"
        assert target.session_count() == 0
    finally:
        target.close()


def test_oversized_session_id_is_rejected_before_any_write(tmp_path):
    target = SessionDB(tmp_path / "target.db")
    try:
        payload = [{
            "id": "s" * (MAX_SESSION_ID_CHARS + 1),
            "source": "import",
            "started_at": 1.0,
            "messages": [{"role": "user", "content": "bad id"}],
        }]
        result = target.import_sessions(payload, enable=True, dry_run=False)

        assert result.ok is False
        assert result.status == "rejected"
        assert target.session_count() == 0
    finally:
        target.close()


def test_unexpected_write_failure_rolls_back_the_entire_batch(tmp_path, monkeypatch):
    target = SessionDB(tmp_path / "target.db")
    try:
        payload = [
            {
                "id": "one",
                "source": "import",
                "started_at": 1.0,
                "messages": [{"role": "user", "content": "one"}],
            },
            {
                "id": "two",
                "source": "import",
                "started_at": 2.0,
                "messages": [{"role": "user", "content": "two"}],
            },
        ]
        calls = {"count": 0}
        original_encode = target._encode_content

        def fail_on_second_message(content):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("simulated import failure")
            return original_encode(content)

        monkeypatch.setattr(target, "_encode_content", fail_on_second_message)
        result = target.import_sessions(payload, enable=True, dry_run=False)

        assert result.ok is False
        assert result.status == "rolled_back"
        assert target.session_count() == 0
    finally:
        target.close()
