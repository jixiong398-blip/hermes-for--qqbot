"""Side-effect-free SessionDB helpers staged from upstream Hermes.

This module is intentionally not imported by :mod:`hermes_state` yet.  It is
the Gate 1 compatibility surface for the three-way SessionDB merge: callers
may exercise deterministic SQL/text construction without changing a live
database, schema version, FTS layout, or OneBot routing.
"""

from __future__ import annotations

import sqlite3
from typing import Any


_SQL_WHITESPACE = "CHAR(9) || CHAR(10) || CHAR(13) || CHAR(32)"


def _schema_script_authorizer(
    action: int,
    arg1: str | None,
    arg2: str | None,
    _database: str | None,
    _source: str | None,
) -> int:
    """Keep schema probing inside SQLite memory and away from extensions."""

    if action in {
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH,
        sqlite3.SQLITE_PRAGMA,
    }:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION:
        function_name = (arg1 or arg2 or "").lower()
        if function_name == "load_extension":
            return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def escape_like(text: str) -> str:
    """Escape SQL LIKE wildcards for use with ``ESCAPE '\\'``."""

    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _sql_literal(text: str) -> str:
    """Return a single-quoted SQL literal with embedded quotes escaped."""

    return "'" + text.replace("'", "''") + "'"


def _sql_ltrim_whitespace(expression: str) -> str:
    """Build a SQLite expression that trims the supported leading whitespace."""

    return f"LTRIM({expression}, {_SQL_WHITESPACE})"


def _sql_trim_whitespace(expression: str) -> str:
    """Build a SQLite expression that trims the supported surrounding whitespace."""

    return f"TRIM({expression}, {_SQL_WHITESPACE})"


def _sql_starts_with(expression: str, prefixes: tuple[str, ...]) -> str:
    """Build a qualified, literal-safe prefix predicate for internal SQL."""

    trimmed = _sql_ltrim_whitespace(expression)
    checks = [
        f"SUBSTR({trimmed}, 1, {len(prefix)}) = {_sql_literal(prefix)}"
        for prefix in prefixes
    ]
    return "(" + " OR ".join(checks) + ")"


_BRANCH_CHILD_SQL = (
    "json_extract(COALESCE({a}.model_config, '{{}}'), '$._branched_from') IS NOT NULL"
    " OR EXISTS (SELECT 1 FROM sessions p"
    "            WHERE p.id = {a}.parent_session_id"
    "            AND p.end_reason = 'branched'"
    "            AND {a}.started_at >= p.ended_at)"
)


_COMPRESSION_CHILD_SQL = (
    "EXISTS (SELECT 1 FROM sessions p"
    "        WHERE p.id = {a}.parent_session_id"
    "        AND p.end_reason = 'compression')"
)


_RESET_CHILD_SQL = (
    "json_extract(COALESCE({a}.model_config, '{{}}'), '$._reset_from') IS NOT NULL"
    " OR "
    "EXISTS (SELECT 1 FROM sessions p"
    "            WHERE p.id = {a}.parent_session_id"
    "            AND p.end_reason IN ('session_reset', 'session_switch', 'idle', "
    "'daily', 'suspended', 'resume_pending_expired')"
    "            AND {a}.session_key IS NOT NULL"
    "            AND {a}.session_key != ''"
    "            AND {a}.session_key = p.session_key)"
)


def _ephemeral_child_sql(alias: str = "s") -> str:
    """Return the upstream predicate for hidden subagent child sessions."""

    branch = _BRANCH_CHILD_SQL.format(a=alias)
    compression = _COMPRESSION_CHILD_SQL.format(a=alias)
    reset = _RESET_CHILD_SQL.format(a=alias)
    return (
        f"({alias}.parent_session_id IS NOT NULL"
        f" AND NOT ({branch})"
        f" AND NOT ({compression})"
        f" AND NOT ({reset}))"
    )


def _sql_session_last_active(alias: str = "s") -> str:
    """Return a recency expression using heartbeat, message time, then start."""

    msg_max = (
        f"(SELECT MAX(_act_m.timestamp) FROM messages _act_m "
        f"WHERE _act_m.session_id = {alias}.id)"
    )
    return (
        "COALESCE("
        "(SELECT MAX(_act_v.v) FROM ("
        f"SELECT {alias}.last_activity_at AS v UNION ALL SELECT {msg_max}"
        ") _act_v), "
        f"{alias}.started_at)"
    )


def _sql_session_last_active_by_id(session_id_expr: str) -> str:
    """Return the recency expression keyed by an internal session-id expression."""

    msg_max = (
        f"(SELECT MAX(_act_m.timestamp) FROM messages _act_m "
        f"WHERE _act_m.session_id = {session_id_expr})"
    )
    activity = (
        f"(SELECT last_activity_at FROM sessions _act_s "
        f"WHERE _act_s.id = {session_id_expr})"
    )
    started = (
        f"(SELECT started_at FROM sessions _act_s "
        f"WHERE _act_s.id = {session_id_expr})"
    )
    return (
        "COALESCE("
        "(SELECT MAX(_act_v.v) FROM ("
        f"SELECT {activity} AS v UNION ALL SELECT {msg_max}"
        f") _act_v), {started})"
    )


def build_schema_read_probe_statements(schema_sql: str) -> tuple[str, ...]:
    """Build ``LIMIT 0`` probes from a schema script without touching disk.

    The schema script is executed in an in-memory SQLite connection solely to
    ask SQLite for its parsed table/column metadata.  Returned statements
    qualify every identifier, avoiding SQLite's double-quoted-string fallback
    that can make a stale-schema probe appear to pass.
    """

    if not isinstance(schema_sql, str):
        raise TypeError("schema_sql must be a string")

    connection = sqlite3.connect(":memory:")
    try:
        connection.set_authorizer(_schema_script_authorizer)
        try:
            connection.executescript(schema_sql)
        finally:
            connection.set_authorizer(None)
        tables = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        statements: list[str] = []
        for (table,) in tables:
            columns = connection.execute(
                "SELECT name FROM pragma_table_info(?) ORDER BY cid", (table,)
            ).fetchall()
            if not columns:
                continue
            quoted_table = table.replace('"', '""')
            qualified = ", ".join(
                f'"{quoted_table}"."{column[0].replace(chr(34), chr(34) * 2)}"'
                for column in columns
            )
            statements.append(f'SELECT {qualified} FROM "{quoted_table}" LIMIT 0')
        return tuple(statements)
    finally:
        connection.close()


__all__ = [
    "build_schema_read_probe_statements",
    "escape_like",
    "_ephemeral_child_sql",
    "_sql_literal",
    "_sql_ltrim_whitespace",
    "_sql_session_last_active",
    "_sql_session_last_active_by_id",
    "_sql_starts_with",
    "_sql_trim_whitespace",
]
