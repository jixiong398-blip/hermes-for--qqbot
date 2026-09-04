"""Canonical search compatibility ports for the local SessionDB facade.

The local ``SessionDB.search_messages`` implementation remains authoritative.
This module exposes upstream-compatible names and a bounded sanitizer without
changing the local FTS/LIKE result shape or opening a database on import.
"""

from __future__ import annotations

try:
    import hermes_bootstrap  # noqa: F401
except ModuleNotFoundError:
    hermes_bootstrap = None  # type: ignore[assignment]

import re
from typing import Any, Collection, Dict, List, Optional

from hermes_state_common import MAX_FTS5_QUERY_CHARS, escape_like


_FTS5_SPECIAL_RE = re.compile(r'[+{}():"^@/#&|~\[\]<>,;!?$=\\\']')


def _sanitize_fts5_query(query: str) -> str:
    """Remove FTS5 grammar controls while keeping a bounded text query."""

    if not isinstance(query, str):
        return ""
    value = query[:MAX_FTS5_QUERY_CHARS]
    # The local facade owns the complete quote/boolean/CJK sanitizer.  Use it
    # only at call time so this canonical module remains import-cycle safe.
    try:
        from hermes_state import SessionDB

        return SessionDB._sanitize_fts5_query(value)
    except Exception:
        value = _FTS5_SPECIAL_RE.sub(" ", value)
        return value.replace("*", " ").strip()


def sanitize_fts5_query(query: str) -> str:
    """Public alias for the upstream/private sanitizer name."""

    return _sanitize_fts5_query(query)


def _contains_cjk(text: str) -> bool:
    if not isinstance(text, str):
        return False
    return any(
        "\u4e00" <= char <= "\u9fff"
        or "\u3400" <= char <= "\u4dbf"
        for char in text
    )


def bounded_search_messages(
    session_db: Any,
    query: str,
    *,
    limit: int = 20,
    offset: int = 0,
    fields: Optional[Collection[str]] = None,
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    """Call a host search port with bounded pagination and fail-closed errors."""

    try:
        bounded_limit = max(1, min(int(limit), 1_000))
        bounded_offset = max(0, min(int(offset), 1_000_000))
    except (TypeError, ValueError, OverflowError):
        return []
    search = getattr(session_db, "search_messages", None)
    if not callable(search):
        return []
    try:
        raw_query = query[:MAX_FTS5_QUERY_CHARS] if isinstance(query, str) else ""
        try:
            result = search(
                raw_query,
                limit=bounded_limit,
                offset=bounded_offset,
                fields=fields,
                **kwargs,
            )
        except TypeError:
            # Older host facades do not expose ``fields`` yet.
            result = search(
                raw_query,
                limit=bounded_limit,
                offset=bounded_offset,
                **kwargs,
            )
        return result if isinstance(result, list) else []
    except Exception:
        return []


class SessionSearchMixin:
    """Import-compatible mixin that delegates to explicit host methods."""

    @staticmethod
    def _sanitize_fts5_query(query: str) -> str:
        return _sanitize_fts5_query(query)

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return _contains_cjk(text)

    def search_messages(self, query: str, **kwargs: Any) -> List[Dict[str, Any]]:
        implementation = getattr(self, "_canonical_search_messages", None)
        if not callable(implementation):
            return []
        try:
            result = implementation(
                query,
                limit=max(1, min(int(kwargs.pop("limit", 20)), 1_000)),
                offset=max(0, min(int(kwargs.pop("offset", 0)), 1_000_000)),
                **kwargs,
            )
            return result if isinstance(result, list) else []
        except Exception:
            return []

    def search_sessions_by_id(self, session_id: str, **kwargs: Any) -> List[Dict[str, Any]]:
        implementation = getattr(self, "_canonical_search_sessions_by_id", None)
        if not callable(implementation):
            return []
        try:
            result = implementation(session_id, **kwargs)
            return result if isinstance(result, list) else []
        except Exception:
            return []


__all__ = [
    "MAX_FTS5_QUERY_CHARS",
    "SessionSearchMixin",
    "_contains_cjk",
    "_sanitize_fts5_query",
    "bounded_search_messages",
    "escape_like",
    "sanitize_fts5_query",
]
