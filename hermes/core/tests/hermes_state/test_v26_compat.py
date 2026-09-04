"""Read-only SessionDB v26 compatibility contract tests."""

from __future__ import annotations

import hashlib
import sqlite3

from hermes_state_v26_compat import (
    LOCAL_V11_SCHEMA,
    V26_SCHEMA_VERSION,
    V26MigrationPlan,
    V26SchemaReport,
    build_v26_migration_plan,
    probe_v26_migration_plan,
    probe_v26_schema,
    schema_delta_from_v11,
    v26_schema_contract,
)


def test_v26_contract_contains_upstream_core_tables_and_columns():
    contract = v26_schema_contract()

    assert V26_SCHEMA_VERSION == 26
    assert {
        "schema_version",
        "system_prompts",
        "sessions",
        "messages",
        "session_model_usage",
        "gateway_routing",
        "gateway_hygiene_state",
        "compression_locks",
        "session_turn_leases",
        "async_delegations",
        "state_meta",
    } <= set(contract)
    assert "session_key" in contract["sessions"]
    assert "effect_disposition" in contract["messages"]
    assert "delivery_claimed_at" in contract["async_delegations"]


def test_v26_delta_is_deterministic_and_does_not_mutate_v11_baseline():
    before = {table: tuple(columns) for table, columns in LOCAL_V11_SCHEMA.items()}
    first = schema_delta_from_v11()
    second = schema_delta_from_v11()

    assert first == second
    assert "system_prompts" in first
    assert "session_key" in first["sessions"]
    assert "effect_disposition" in first["messages"]
    assert LOCAL_V11_SCHEMA == before


def test_probe_identifies_local_v11_as_legacy_without_writing(tmp_path):
    path = tmp_path / "state.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version(version) VALUES (11);
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT);
        CREATE TABLE state_meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    connection.commit()
    connection.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    report = probe_v26_schema(path)

    assert isinstance(report, V26SchemaReport)
    assert report.schema_version == 11
    assert report.state == "legacy_v11"
    assert report.migration_required is True
    assert "system_prompts" in report.missing_tables
    assert ("sessions", "session_key") in report.missing_columns
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_probe_recognizes_complete_v26_shape(tmp_path):
    path = tmp_path / "v26.db"
    connection = sqlite3.connect(path)
    for table, columns in v26_schema_contract().items():
        if table == "schema_version":
            connection.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
            connection.execute("INSERT INTO schema_version(version) VALUES (26)")
            continue
        declarations = ", ".join(f'"{column}" TEXT' for column in columns)
        connection.execute(f'CREATE TABLE "{table}" ({declarations})')
    connection.commit()
    connection.close()

    report = probe_v26_schema(path)

    assert report == V26SchemaReport(26, (), (), ())
    assert report.is_v26_ready is True
    assert report.state == "v26_ready"


def test_probe_missing_database_is_read_only_and_bounded(tmp_path):
    path = tmp_path / "missing.db"

    report = probe_v26_schema(path)

    assert report.is_v26_ready is False
    assert report.state == "unreadable"
    assert report.errors
    assert not path.exists()


def test_sessiondb_facade_exposes_read_only_v26_probe(tmp_path):
    from hermes_state import SessionDB

    db = SessionDB(tmp_path / "state.db")
    try:
        report = db.probe_v26_compatibility()
        assert report.state == "legacy_v11"
        assert report.schema_version == 11
        assert report.migration_required is True
    finally:
        db.close()


def test_v11_migration_plan_is_copy_only_and_lists_additive_work(tmp_path):
    path = tmp_path / "state.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version(version) VALUES (11);
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT);
        CREATE TABLE state_meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    connection.commit()
    connection.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    plan = probe_v26_migration_plan(path)

    assert isinstance(plan, V26MigrationPlan)
    assert plan.source_state == "legacy_v11"
    assert plan.source_schema_version == 11
    assert plan.requires_migration is True
    assert plan.replayable_on_copy is True
    assert plan.is_read_only is True
    assert plan.requires_copied_database is True
    assert plan.write_gate_open is False
    assert "system_prompts" in plan.add_tables
    assert ("sessions", "session_key") in plan.add_columns
    assert "schema_version_write" in plan.deferred_operations
    assert "replay_into_disposable_target" in plan.next_steps
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_complete_v26_plan_has_no_additive_candidates_but_keeps_write_gate_closed():
    report = V26SchemaReport(26, (), (), ())

    plan = build_v26_migration_plan(report)

    assert plan.source_state == "v26_ready"
    assert plan.requires_migration is False
    assert plan.replayable_on_copy is True
    assert plan.add_tables == ()
    assert plan.add_columns == ()
    assert plan.blockers == ()
    assert plan.write_gate_open is False
    assert plan.is_read_only is True
    assert "compare_fts_indexes_and_triggers_on_copy" in plan.next_steps


def test_future_schema_and_probe_errors_are_hard_blockers():
    future = build_v26_migration_plan(V26SchemaReport(27, (), (), ()))
    assert future.source_state == "unknown_schema"
    assert future.replayable_on_copy is False
    assert "future_schema_version" in future.blockers

    unreadable = build_v26_migration_plan(
        V26SchemaReport(11, (), (), ("DatabaseError: malformed",))
    )
    assert unreadable.source_state == "unreadable"
    assert unreadable.replayable_on_copy is False
    assert "schema_probe_errors" in unreadable.blockers


def test_sessiondb_plan_facade_does_not_change_v11_schema(tmp_path):
    from hermes_state import SessionDB

    path = tmp_path / "facade.db"
    db = SessionDB(db_path=path)
    try:
        before_version = db._conn.execute(
            "SELECT version FROM schema_version LIMIT 1"
        ).fetchone()[0]
        before_tables = tuple(
            row[0]
            for row in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        )

        plan = db.plan_v26_migration()

        after_version = db._conn.execute(
            "SELECT version FROM schema_version LIMIT 1"
        ).fetchone()[0]
        after_tables = tuple(
            row[0]
            for row in db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        )
    finally:
        db.close()

    assert plan.source_state == "legacy_v11"
    assert before_version == after_version == 11
    assert before_tables == after_tables
