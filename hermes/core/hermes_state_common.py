"""Canonical import-compatible common ports for the local SessionDB facade.

The local product still owns the v11 ``SCHEMA_SQL`` and runtime transaction
behavior.  This module contains only side-effect-free helper contracts; it
does not import ``hermes_state`` and never opens a database.
"""

from __future__ import annotations

try:
    import hermes_bootstrap  # noqa: F401
except ModuleNotFoundError:
    hermes_bootstrap = None  # type: ignore[assignment]

from contextlib import contextmanager
from typing import Any, Iterator

from hermes_state_common_compat import (
    _ephemeral_child_sql,
    _sql_literal,
    _sql_ltrim_whitespace,
    _sql_session_last_active,
    _sql_session_last_active_by_id,
    _sql_starts_with,
    _sql_trim_whitespace,
    build_schema_read_probe_statements,
    escape_like,
)


# The canonical module is import-compatible, but the local facade remains the
# source of truth.  Keep both version labels explicit so callers cannot mistake
# a capability port for a v26 migration.
SCHEMA_VERSION = 11
LOCAL_SCHEMA_VERSION = 11
UPSTREAM_SCHEMA_VERSION = 26
MAX_FTS5_QUERY_CHARS = 4096
FTS_STORAGE_VERSION = 0

# Schema/FTS text is intentionally not copied from the local monolith.  The
# schema adapter accepts an explicit script or lazily obtains the host script at
# call time; common helpers stay free of a facade import cycle.
# These are intentionally empty compatibility markers.  The local schema and
# FTS scripts stay owned by ``hermes_state.py``; callers needing those scripts
# must use ``get_local_schema_sql``/``get_local_fts_sql`` explicitly.  Keeping
# them out of the import path prevents a common -> facade cycle.
SCHEMA_SQL = ""
DEFERRED_INDEX_SQL = ""
FTS_SQL = ""
FTS_TRIGRAM_SQL = ""
LEGACY_FTS_SQL = ""
LEGACY_FTS_TRIGRAM_SQL = ""
FTS_CJK_STALE_KEY = "fts_cjk_stale"
FTS_STALE_KEY = "fts_stale"
FTS_REBUILD_DEFERRAL_KEY = "fts_rebuild_deferral"

# Mixin modules import these names to describe optional derived-index state.
_FTS_TRIGGERS: tuple[str, ...] = ()
_FTS_CJK_TRIGGERS: tuple[str, ...] = ()

_PREVIEW_MAX_CHARS = 60
_PREVIEW_HEAD_CHARS = 63
_PREVIEW_SCAFFOLD_WINDOW = 400
_PREVIEW_ELIGIBLE_SQL = "m.content IS NOT NULL"
_PREVIEW_RAW_SELECT = (
    "SUBSTR(REPLACE(REPLACE(m.content, X'0A', ' '), X'0D', ' '), 1, 63)"
)


def _shape_preview(raw: Any) -> str:
    """Return a bounded one-line preview for listing/export ports."""

    text = str(raw or "").replace("\n", " ").replace("\r", " ").strip()
    return text[:_PREVIEW_MAX_CHARS]


_RESET_END_REASONS = (
    "session_reset",
    "session_switch",
    "idle",
    "daily",
    "suspended",
    "resume_pending_expired",
)
_RESET_END_REASONS_SQL = ", ".join(
    f"'{reason}'" for reason in _RESET_END_REASONS
)
_RECOVERABLE_END_REASONS = (
    "agent_close",
    "ws_orphan_reap",
    "superseded_by_resume",
    "startup_orphan_reap",
)
_RECOVERABLE_END_REASONS_SQL = ", ".join(
    f"'{reason}'" for reason in _RECOVERABLE_END_REASONS
)


def _legacy_reset_child_sql(alias: str, reasons_sql: str = _RESET_END_REASONS_SQL) -> str:
    """Return the bounded same-routing-key reset-child predicate."""

    if not isinstance(alias, str) or not alias or not alias.replace("_", "").isalnum():
        raise ValueError("SQL alias is invalid")
    if not isinstance(reasons_sql, str) or not reasons_sql:
        raise ValueError("reset reasons are invalid")
    return (
        f"EXISTS (SELECT 1 FROM sessions p WHERE p.id = {alias}.parent_session_id "
        f"AND p.end_reason IN ({reasons_sql}) "
        f"AND {alias}.session_key IS NOT NULL "
        f"AND {alias}.session_key != '' "
        f"AND {alias}.session_key = p.session_key)"
    )


@contextmanager
def fts_rebuild_admission(_db_path: Any) -> Iterator[bool]:
    """Fail-closed placeholder until the local FTS admission gate is split."""

    yield False


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
    "LOCAL_SCHEMA_VERSION",
    "MAX_FTS5_QUERY_CHARS",
    "SCHEMA_SQL",
    "SCHEMA_VERSION",
    "UPSTREAM_SCHEMA_VERSION",
    "_FTS_CJK_TRIGGERS",
    "_FTS_TRIGGERS",
    "_RESET_END_REASONS",
    "_RESET_END_REASONS_SQL",
    "_RECOVERABLE_END_REASONS",
    "_RECOVERABLE_END_REASONS_SQL",
    "_PREVIEW_ELIGIBLE_SQL",
    "_PREVIEW_RAW_SELECT",
    "_ephemeral_child_sql",
    "_legacy_reset_child_sql",
    "_shape_preview",
    "_sql_literal",
    "_sql_ltrim_whitespace",
    "_sql_session_last_active",
    "_sql_session_last_active_by_id",
    "_sql_starts_with",
    "_sql_trim_whitespace",
    "build_schema_read_probe_statements",
    "escape_like",
    "fts_rebuild_admission",
]
