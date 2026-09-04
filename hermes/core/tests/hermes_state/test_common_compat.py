"""Contract tests for the staged SessionDB common helper boundary."""

from __future__ import annotations

import sqlite3

import pytest

from hermes_state_common_compat import (
    _ephemeral_child_sql,
    _sql_literal,
    _sql_session_last_active,
    _sql_session_last_active_by_id,
    _sql_starts_with,
    _sql_trim_whitespace,
    build_schema_read_probe_statements,
    escape_like,
)


def test_escape_like_quotes_all_like_wildcards_and_escape_characters():
    assert escape_like(r"path\_%") == r"path\\\_\%"


def test_sql_literal_and_whitespace_helpers_are_deterministic():
    assert _sql_literal("O'Reilly") == "'O''Reilly'"
    assert _sql_trim_whitespace("value").startswith("TRIM(value,")
    assert _sql_starts_with("value", ("/new", "[summary]")) == (
        "(SUBSTR(LTRIM(value, CHAR(9) || CHAR(10) || CHAR(13) || CHAR(32)), 1, 4) = '/new' "
        "OR SUBSTR(LTRIM(value, CHAR(9) || CHAR(10) || CHAR(13) || CHAR(32)), 1, 9) = '[summary]')"
    )


def test_session_predicates_keep_alias_and_lineage_boundaries():
    ephemeral = _ephemeral_child_sql("child")
    assert "child.parent_session_id IS NOT NULL" in ephemeral
    assert "child.session_key = p.session_key" in ephemeral
    assert "json_extract(COALESCE(child.model_config" in ephemeral

    active = _sql_session_last_active("session_row")
    assert "session_row.last_activity_at" in active
    assert "_act_m.session_id = session_row.id" in active
    assert "session_row.started_at" in active

    active_by_id = _sql_session_last_active_by_id("candidate.id")
    assert "_act_s.id = candidate.id" in active_by_id
    assert "_act_m.session_id = candidate.id" in active_by_id


def test_schema_probe_is_in_memory_qualified_and_executable():
    schema = """
    CREATE TABLE zeta (id INTEGER PRIMARY KEY, value TEXT);
    CREATE TABLE alpha (name TEXT, percent_value TEXT);
    """
    statements = build_schema_read_probe_statements(schema)

    assert statements == (
        'SELECT "alpha"."name", "alpha"."percent_value" FROM "alpha" LIMIT 0',
        'SELECT "zeta"."id", "zeta"."value" FROM "zeta" LIMIT 0',
    )

    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(schema)
        for statement in statements:
            connection.execute(statement)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "schema",
    [
        "ATTACH DATABASE 'outside.db' AS external_db;",
        "PRAGMA journal_mode=WAL;",
        "SELECT load_extension('external');",
    ],
)
def test_schema_probe_rejects_external_or_extension_actions(schema):
    with pytest.raises(sqlite3.DatabaseError):
        build_schema_read_probe_statements(schema)


@pytest.mark.parametrize("value", [None, 1, object()])
def test_schema_probe_rejects_non_text_schema(value):
    with pytest.raises(TypeError):
        build_schema_read_probe_statements(value)  # type: ignore[arg-type]
