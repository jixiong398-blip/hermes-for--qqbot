"""Read-only SessionDB schema capability probing.

The probe is staged for the Gate 2 three-way merge.  It deliberately does not
import or modify ``hermes_state.SessionDB``: callers can inspect an existing
database before deciding whether a later, explicitly-enabled migration is
safe.  No DDL, FTS rebuild, WAL checkpoint, or schema-version write occurs.
"""

from __future__ import annotations

import re
import sqlite3
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_ERROR_TEXT = 200
_MAX_SIDECAR_BYTES = 8 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class SchemaProbeResult:
    """Immutable result of a schema capability probe."""

    schema_version: Optional[int]
    missing_tables: tuple[str, ...]
    missing_columns: tuple[tuple[str, str], ...]
    errors: tuple[str, ...]

    @property
    def ready(self) -> bool:
        """Return true only when every expected object was readable."""

        return not (
            self.missing_tables or self.missing_columns or self.errors
        )


def _quote_identifier(identifier: str) -> str:
    if not isinstance(identifier, str) or not identifier or "\x00" in identifier:
        raise ValueError("schema identifiers must be non-empty text")
    if not _IDENTIFIER_RE.fullmatch(identifier):
        raise ValueError(f"unsupported schema identifier: {identifier!r}")
    return f'"{identifier}"'


def _normalize_expected_schema(
    expected_schema: Mapping[str, Iterable[str]],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if not isinstance(expected_schema, Mapping):
        raise TypeError("expected_schema must be a mapping")

    normalized: list[tuple[str, tuple[str, ...]]] = []
    for table, columns in expected_schema.items():
        _quote_identifier(table)
        if isinstance(columns, (str, bytes)):
            raise TypeError(f"columns for {table!r} must be an iterable of names")
        column_names = tuple(columns)
        for column in column_names:
            _quote_identifier(column)
        normalized.append((table, column_names))
    return tuple(sorted(normalized))


def _safe_error(error: BaseException) -> str:
    """Keep probe diagnostics bounded and free of the requested DB path."""

    detail = str(error).replace("\r", " ").replace("\n", " ")
    if len(detail) > _MAX_ERROR_TEXT:
        detail = detail[:_MAX_ERROR_TEXT] + "..."
    return f"{type(error).__name__}: {detail}"


def _open_read_only(path: Path) -> sqlite3.Connection:
    """Open an existing regular file through SQLite's read-only URI mode."""

    if path.is_symlink():
        raise ValueError("database symlink paths are not accepted")
    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("database path is not a regular file")
    # A closed database without a WAL does not need SQLite shared-memory
    # coordination. Immutable read-only mode prevents a diagnostic probe from
    # creating a new ``-shm`` sidecar. If a WAL exists, keep ordinary mode=ro
    # so the committed WAL frames remain visible to the probe.
    present_sidecars = False
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(f"{resolved}{suffix}")
        try:
            sidecar_info = sidecar.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise ValueError("database SQLite sidecar could not be inspected") from error
        present_sidecars = True
        if stat.S_ISLNK(sidecar_info.st_mode):
            raise ValueError("database SQLite sidecar symlink paths are not accepted")
        if not stat.S_ISREG(sidecar_info.st_mode):
            raise ValueError("database SQLite sidecar must be a regular file")
        if int(sidecar_info.st_size) > _MAX_SIDECAR_BYTES:
            raise ValueError("database SQLite sidecar exceeds the probe size limit")
    query = "mode=ro" if present_sidecars else "mode=ro&immutable=1"
    uri = f"{resolved.as_uri()}?{query}"
    connection = sqlite3.connect(uri, uri=True, timeout=1.0)
    connection.row_factory = sqlite3.Row
    return connection


def probe_schema(
    db_path: str | Path,
    expected_schema: Mapping[str, Iterable[str]],
) -> SchemaProbeResult:
    """Inspect *db_path* without executing any schema mutation.

    ``expected_schema`` is an operator-owned capability description, not SQL
    text.  Identifiers are validated and quoted before SQLite metadata reads.
    Opening a missing, symlinked, or malformed database returns a non-ready
    result with bounded diagnostics instead of silently treating it as ready.
    """

    normalized = _normalize_expected_schema(expected_schema)
    try:
        path = Path(db_path)
    except (TypeError, ValueError) as error:
        return SchemaProbeResult(None, (), (), (_safe_error(error),))

    connection: sqlite3.Connection | None = None
    errors: list[str] = []
    missing_tables: list[str] = []
    missing_columns: list[tuple[str, str]] = []
    schema_version: Optional[int] = None
    try:
        connection = _open_read_only(path)
        table_rows = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' ORDER BY name"
        ).fetchall()
        available_tables = {str(row[0]) for row in table_rows}

        for table, columns in normalized:
            if table not in available_tables:
                missing_tables.append(table)
                continue
            quoted_table = _quote_identifier(table)
            rows = connection.execute(
                f"PRAGMA table_info({quoted_table})"
            ).fetchall()
            available_columns = {str(row[1]) for row in rows}
            missing_columns.extend(
                (table, column)
                for column in columns
                if column not in available_columns
            )

        if "schema_version" in available_tables:
            row = connection.execute(
                'SELECT "version" FROM "schema_version" LIMIT 1'
            ).fetchone()
            if row is not None:
                try:
                    schema_version = int(row[0])
                except (TypeError, ValueError) as error:
                    errors.append(_safe_error(error))
    except (OSError, sqlite3.Error, ValueError) as error:
        errors.append(_safe_error(error))
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error as error:  # pragma: no cover - defensive
                errors.append(_safe_error(error))

    return SchemaProbeResult(
        schema_version=schema_version,
        missing_tables=tuple(sorted(set(missing_tables))),
        missing_columns=tuple(sorted(set(missing_columns))),
        errors=tuple(errors),
    )


__all__ = ["SchemaProbeResult", "probe_schema"]
