"""Import and contract tests for the staged SessionDB module ports."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from hermes_state_common import (
    UPSTREAM_SCHEMA_VERSION,
    _RECOVERABLE_END_REASONS,
    _ephemeral_child_sql,
    _legacy_reset_child_sql,
)
from hermes_state_portability import (
    PortabilityImportResult,
    SessionPortabilityMixin,
    audit_portable_export,
    dry_run_import,
)
from hermes_state_portability_compat import IMPORT_MAX_SESSIONS
from hermes_state_schema import SessionSchemaMixin, schema_read_probe_statements
from hermes_state_search import (
    MAX_FTS5_QUERY_CHARS,
    SessionSearchMixin,
    _sanitize_fts5_query,
    bounded_search_messages,
)


def _payload(session_id="s1"):
    return {
        "id": session_id,
        "source": "cli",
        "model": "test-model",
        "started_at": 1.0,
        "messages": [{"role": "user", "content": "portable"}],
    }


def test_canonical_modules_import_without_loading_facade():
    core_dir = Path(__file__).parents[2]
    code = (
        "import sys\n"
        "import hermes_state_common\n"
        "import hermes_state_schema\n"
        "import hermes_state_search\n"
        "import hermes_state_portability\n"
        "assert 'hermes_state' not in sys.modules\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=core_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "ok"


def test_common_and_schema_ports_expose_local_lazy_probe():
    assert UPSTREAM_SCHEMA_VERSION == 26
    assert "parent_session_id" in _ephemeral_child_sql("child")
    assert "session_reset" in _legacy_reset_child_sql("child")
    assert "agent_close" in _RECOVERABLE_END_REASONS

    from hermes_state import SCHEMA_SQL

    local_schema = SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS sessions" in local_schema
    statements = schema_read_probe_statements()
    assert statements
    assert any('"sessions"' in statement for statement in statements)
    assert SessionSchemaMixin.schema_read_probe_statements(local_schema) == statements


def test_sessiondb_escape_like_facade_delegates_pure_helper_without_shape_change(
    tmp_path, monkeypatch
):
    import hermes_state_common
    from hermes_state import SessionDB

    original = hermes_state_common.escape_like
    calls = []

    def traced(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(hermes_state_common, "escape_like", traced)
    db = SessionDB(tmp_path / "state.db")
    try:
        assert db.escape_like(r"literal\_%") == r"literal\\\_\%"
        db.create_session("literal%id", source="cli")
        db.create_session("literalXid", source="cli")

        # The LIKE wildcard is escaped by the canonical helper, so only the
        # literal-percent session matches this prefix.
        assert db.resolve_session_id("literal%") == "literal%id"
        assert "literal%" in calls
    finally:
        db.close()


def test_sessiondb_append_message_preserves_explicit_event_timestamp(tmp_path):
    from hermes_state import SessionDB

    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("timestamp-session", source="onebot")
        db.append_message(
            "timestamp-session",
            role="user",
            content="event-time",
            timestamp=123.5,
        )
        rows = db.get_messages("timestamp-session")
        assert rows[-1]["timestamp"] == 123.5
    finally:
        db.close()


def test_search_port_delegates_local_sanitizer_and_bounds_host_pagination():
    from hermes_state import SessionDB

    query = '"quoted phrase" AND unsafe: token'
    assert _sanitize_fts5_query(query) == SessionDB._sanitize_fts5_query(query)
    assert len(_sanitize_fts5_query("x" * (MAX_FTS5_QUERY_CHARS + 100))) <= MAX_FTS5_QUERY_CHARS

    calls = []

    class Host:
        def search_messages(self, *args, **kwargs):
            calls.append((args, kwargs))
            return [{"id": 1}]

    result = bounded_search_messages(
        Host(),
        "query",
        limit=999999,
        offset=-10,
        fields=("id",),
    )
    assert result == [{"id": 1}]
    assert calls[0][0] == ("query",)
    assert calls[0][1]["limit"] == 1000
    assert calls[0][1]["offset"] == 0


def test_search_mixin_uses_explicit_host_hook_without_recursion():
    class Host(SessionSearchMixin):
        def _canonical_search_messages(self, query, **kwargs):
            return [{"query": query, **kwargs}]

    result = Host().search_messages("hello", limit=999999, offset=-1)
    assert result[0]["query"] == "hello"
    assert result[0]["limit"] == 1000
    assert result[0]["offset"] == 0


def test_portability_canonical_ports_audit_dry_run_and_reject_custom_limits():
    payload = _payload()
    audit = audit_portable_export(payload)
    assert audit.ok is True
    assert audit.session_count == 1

    result = dry_run_import(payload)
    assert isinstance(result, PortabilityImportResult)
    assert result.ok is True
    assert result.status == "dry_run"

    rejected = dry_run_import(
        [_payload("a"), _payload("b")],
        max_sessions=1,
    )
    assert rejected.ok is False
    assert rejected.status == "rejected"
    assert rejected.audit is not None
    assert any("at most 1" in error for error in rejected.audit.errors)
    assert IMPORT_MAX_SESSIONS >= 2

def test_portability_mixin_delegates_only_explicit_hooks():
    payload = _payload()
    mixin = SessionPortabilityMixin()
    assert mixin.export_session("missing") is None
    assert mixin.export_all() == []
    result = mixin.import_sessions(payload, enable=True, dry_run=True)
    assert isinstance(result, PortabilityImportResult)
    assert result.status == "dry_run"
    assert mixin.audit_export_payload(payload).ok is True

    class Hooked(SessionPortabilityMixin):
        def _canonical_export_session(self, session_id):
            return {"id": session_id}

        def _canonical_export_all(self, source=None):
            return [{"source": source}]

    hooked = Hooked()
    assert hooked.export_session("s1") == {"id": "s1"}
    assert hooked.export_all(source="cli") == [{"source": "cli"}]


def test_sessiondb_facade_uses_canonical_portability_module_without_shape_change(tmp_path):
    from hermes_state import SessionDB

    database = SessionDB(tmp_path / "state.db")
    try:
        database.create_session("s1", source="cli")
        database.append_message("s1", "user", "portable")
        exported = database.export_session("s1")
        audit = database.audit_export_payload(exported)
        assert audit.ok is True
        assert json.loads(json.dumps(exported, ensure_ascii=False))["id"] == "s1"
    finally:
        database.close()
