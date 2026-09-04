"""Canonical schema/FTS compatibility ports for the local SessionDB.

This is an adapter boundary, not the upstream v26 schema implementation.  It
never mutates a database during import and does not import ``hermes_state``
until a caller explicitly asks for the local schema script.
"""

from __future__ import annotations

try:
    import hermes_bootstrap  # noqa: F401
except ModuleNotFoundError:
    hermes_bootstrap = None  # type: ignore[assignment]

import sqlite3
from typing import Any, Dict, Iterable, Mapping, Optional

from hermes_state_common import (
    DEFERRED_INDEX_SQL,
    FTS_CJK_STALE_KEY,
    FTS_REBUILD_DEFERRAL_KEY,
    FTS_SQL,
    FTS_STORAGE_VERSION,
    FTS_STALE_KEY,
    FTS_TRIGRAM_SQL,
    LEGACY_FTS_SQL,
    LEGACY_FTS_TRIGRAM_SQL,
    SCHEMA_SQL,
    SCHEMA_VERSION,
    _ephemeral_child_sql,
    build_schema_read_probe_statements,
    fts_rebuild_admission,
)
from hermes_state_schema_probe import SchemaProbeResult, probe_schema


_READ_PROBE_STATEMENTS: Optional[tuple[str, ...]] = None
_FTS_TRIGGERS: tuple[str, ...] = ()
_FTS_CJK_TRIGGERS: tuple[str, ...] = ()
_FTS_BASE_TRIGGERS: tuple[str, ...] = ()
_FTS_TRIGRAM_TRIGGERS: tuple[str, ...] = ()


def schema_read_probe_statements(
    schema_sql: Optional[str] = None,
) -> tuple[str, ...]:
    """Return bounded ``LIMIT 0`` probes for an explicit schema script.

    When no script is passed, the local v11 facade is imported lazily.  This
    keeps module import side-effect free while retaining the upstream helper's
    convenient no-argument form for callers outside the facade.
    """

    global _READ_PROBE_STATEMENTS
    explicit_schema = schema_sql is not None
    if schema_sql is None:
        if _READ_PROBE_STATEMENTS is not None:
            return _READ_PROBE_STATEMENTS
        try:
            from hermes_state import SCHEMA_SQL as schema_sql  # lazy adapter edge
        except Exception:
            return ()
    if not isinstance(schema_sql, str):
        raise TypeError("schema_sql must be a string")
    statements = build_schema_read_probe_statements(schema_sql)
    if explicit_schema:
        return statements
    _READ_PROBE_STATEMENTS = statements
    return statements


def _schema_authorizer(
    action: int,
    arg1: Optional[str],
    arg2: Optional[str],
    _database: Optional[str],
    _source: Optional[str],
) -> int:
    if action in {
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH,
        sqlite3.SQLITE_PRAGMA,
    }:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg1 or arg2 or "").lower() == "load_extension":
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _parse_schema_columns(
    schema_sql: Optional[str] = None,
) -> Dict[str, Dict[str, str]]:
    """Parse an explicit schema script in memory without touching disk."""

    if schema_sql is None:
        try:
            from hermes_state import SCHEMA_SQL as schema_sql
        except Exception:
            return {}
    if not isinstance(schema_sql, str):
        raise TypeError("schema_sql must be a string")
    connection = sqlite3.connect(":memory:")
    try:
        connection.set_authorizer(_schema_authorizer)
        try:
            connection.executescript(schema_sql)
        finally:
            connection.set_authorizer(None)
        result: Dict[str, Dict[str, str]] = {}
        tables = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for (table,) in tables:
            columns: Dict[str, str] = {}
            for row in connection.execute(
                f'PRAGMA table_info("{str(table).replace(chr(34), chr(34) * 2)}")'
            ).fetchall():
                column_type = str(row[2] or "")
                if row[3]:
                    column_type += " NOT NULL"
                if row[4] is not None:
                    column_type += f" DEFAULT {row[4]}"
                columns[str(row[1])] = column_type
            result[str(table)] = columns
        return result
    finally:
        connection.close()


class SessionSchemaMixin:
    """Small mixin port that delegates only to explicit host capabilities."""

    @staticmethod
    def schema_read_probe_statements(schema_sql: Optional[str] = None) -> tuple[str, ...]:
        return schema_read_probe_statements(schema_sql)

    @staticmethod
    def probe_schema(
        db_path: str,
        expected_schema: Mapping[str, Iterable[str]],
    ) -> SchemaProbeResult:
        return probe_schema(db_path, expected_schema)

    @classmethod
    def _parse_schema_columns(
        cls,
        schema_sql: Optional[str] = None,
    ) -> Dict[str, Dict[str, str]]:
        return _parse_schema_columns(schema_sql)

    def fts_rebuild_status(self) -> Optional[Mapping[str, Any]]:
        """Read a host-provided status hook, or return an inert status."""

        hook = getattr(self, "_canonical_fts_rebuild_status", None)
        return hook() if callable(hook) else None

    def fts_rebuild_step(self) -> bool:
        """Return whether a host explicitly supplied more rebuild work."""

        hook = getattr(self, "_canonical_fts_rebuild_step", None)
        return bool(hook()) if callable(hook) else False

    def rebuild_fts(self) -> int:
        """Delegate only to a host-owned rebuild implementation."""

        hook = getattr(self, "_canonical_rebuild_fts", None)
        return int(hook()) if callable(hook) else 0

    def fts_optimize_available(self) -> bool:
        hook = getattr(self, "_canonical_fts_optimize_available", None)
        return bool(hook()) if callable(hook) else False

    def optimize_fts(self) -> int:
        hook = getattr(self, "_canonical_optimize_fts", None)
        return int(hook()) if callable(hook) else 0

    def fts_cjk_rebuild_status(self) -> Optional[Mapping[str, Any]]:
        hook = getattr(self, "_canonical_fts_cjk_rebuild_status", None)
        return hook() if callable(hook) else None

    def fts_cjk_rebuild_step(self) -> bool:
        hook = getattr(self, "_canonical_fts_cjk_rebuild_step", None)
        return bool(hook()) if callable(hook) else False


__all__ = [
    "DEFERRED_INDEX_SQL",
    "FTS_CJK_STALE_KEY",
    "FTS_REBUILD_DEFERRAL_KEY",
    "FTS_SQL",
    "FTS_STORAGE_VERSION",
    "FTS_STALE_KEY",
    "FTS_TRIGRAM_SQL",
    "LEGACY_FTS_SQL",
    "LEGACY_FTS_TRIGRAM_SQL",
    "SCHEMA_SQL",
    "SCHEMA_VERSION",
    "SchemaProbeResult",
    "SessionSchemaMixin",
    "_FTS_BASE_TRIGGERS",
    "_FTS_CJK_TRIGGERS",
    "_FTS_TRIGGERS",
    "_FTS_TRIGRAM_TRIGGERS",
    "_ephemeral_child_sql",
    "_parse_schema_columns",
    "fts_rebuild_admission",
    "probe_schema",
    "schema_read_probe_statements",
]
