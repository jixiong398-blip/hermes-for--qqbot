"""Corpus history search over OneBot QQ group chat messages.

Provides idempotent FTS5 initialisation, a rebuild/backfill function,
and a parameterised search with short-CJK LIKE fallback when trigram
tokenisation yields no results for queries with fewer than 3 CJK chars.

All functions accept a ``db`` or ``db_path`` argument so they are
testable without a live state.db on disk.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from hermes_constants import get_state_db_path

logger = logging.getLogger(__name__)

# ── CJK Unicode ranges (excerpts) ──────────────────────────────────────────
_CJK_RE = re.compile(
    r"[\u3040-\u30ff\u31f0-\u31ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\uf900-\ufaff\uff00-\uffef\u3000-\u303f\u2e80-\u2eff"  # noqa: E501
    r"\u2f00-\u2fdf\u3100-\u312f\u3200-\u32ff\u3300-\u33ff"
    r"\uac00-\ud7af\U0001b000-\U0001b0ff"
    r"\u2000-\u206f\u2ff0-\u2fff\ua960-\ua97f\ud7b0-\ud7ff"
    r"\uf920-\uf929\U0001f200-\U0001f2ff\U0001f600-\U0001f64f]",
)

# Guardrails  (hard caps on tool inputs)
_MAX_QUERY_CHARS = 200
_MAX_DB_LIMIT = 100
_DEFAULT_TOOL_LIMIT = 8
_MAX_PREVIEW_CHARS = 120

_fts_init_attempted: set[str] = set()  # db_path strings we've already tried


def _reset_fts_cache() -> None:
    """Reset the FTS init tracking set (test support)."""
    _fts_init_attempted.clear()


def _count_cjk(query: str) -> int:
    """Return the number of CJK codepoints in *query*."""
    return len(_CJK_RE.findall(query))


def _sanitise_query(raw: str) -> str:
    """Strip, truncate to ``_MAX_QUERY_CHARS`` chars, and collapse whitespace."""
    q = " ".join(raw.strip().split())[:_MAX_QUERY_CHARS]
    return q



# ── Idempotent FTS initialisation ──────────────────────────────────────────


def init_fts(db_path: str | Path) -> dict:
    """Create FTS tables, indexes, and triggers if they don't already exist.

    Idempotent — safe to call multiple times against the same database.
    Returns a status dict with ``fts_created``, ``index_created``, ``triggers_created``.
    """
    path = str(db_path)
    result: dict[str, bool | str | None] = {
        "fts_created": False,
        "index_created": False,
        "triggers_created": False,
        "error": None,
    }
    db = sqlite3.connect(path, timeout=10)
    try:
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA journal_mode=WAL")

        # 0. Gate: corpus_messages must exist before creating indexes or FTS.
        cur = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='corpus_messages'"
        )
        if cur.fetchone() is None:
            result["error"] = "corpus_messages table not found — cannot create FTS"
            return result

        # 1. Index on message_id (for reply lookups).
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_corpus_message_id "
            "ON corpus_messages(message_id)"
        )
        result["index_created"] = True

        # 2. FTS5 virtual table (trigram tokenizer for CJK substring matching).

        db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS corpus_messages_fts USING fts5("
            "content_readable, sender_name, "
            "content='corpus_messages', content_rowid='id', "
            "tokenize='trigram'"
            ")"
        )
        result["fts_created"] = True

        # 3. INSERT trigger so new rows land in the FTS index automatically.
        db.execute(
            "CREATE TRIGGER IF NOT EXISTS corpus_fts_ai "
            "AFTER INSERT ON corpus_messages "
            "BEGIN "
            "INSERT INTO corpus_messages_fts(rowid, content_readable, sender_name) "
            "VALUES (new.id, new.content_readable, new.sender_name); "
            "END"
        )
        result["triggers_created"] = True

        db.commit()
    except sqlite3.OperationalError as exc:
        result["error"] = str(exc)
        logger.warning("init_fts: %s", exc)
    finally:
        db.close()
    return result


# ── Rebuild / backfill ─────────────────────────────────────────────────────


def rebuild_fts(db_path: str | Path) -> dict:
    """Repopulate corpus_messages_fts from corpus_messages.

    Idempotent — calls :func:`init_fts` first, then does an INSERT … SELECT
    that re-populates the FTS table.  Returns row counts and status.

    Does NOT raise for normal missing-schema cases (no corpus_messages, etc.).
    """
    path = str(db_path)
    result: dict[str, Any] = {
        "source_rows": 0,
        "fts_rows": 0,
        "error": None,
    }
    init_result = init_fts(path)
    if init_result.get("error"):
        result["error"] = init_result["error"]
        return result
    result.update(init_result)

    db = sqlite3.connect(path, timeout=10)
    try:
        db.execute("PRAGMA busy_timeout=30000")

        # Count source
        cur = db.execute("SELECT COUNT(*) FROM corpus_messages")
        result["source_rows"] = cur.fetchone()[0]

        # Rebuild FTS via the FTS5 rebuild command.  This re-indexes all
        # content from the content table (corpus_messages) into the FTS
        # index, honouring the content= / content_rowid= declared when the
        # virtual table was created.  Truly idempotent — safe to run
        # multiple times.
        db.execute(
            "INSERT INTO corpus_messages_fts(corpus_messages_fts) VALUES('rebuild')"
        )
        db.commit()

        cur = db.execute("SELECT COUNT(*) FROM corpus_messages_fts")
        result["fts_rows"] = cur.fetchone()[0]
    except sqlite3.OperationalError as exc:
        result["error"] = str(exc)
        logger.warning("rebuild_fts: %s", exc)
    finally:
        db.close()
    return result


# ── Search ─────────────────────────────────────────────────────────────────


def _fts_search(
    db: sqlite3.Connection,
    query: str,
    limit: int,
    group_id: str | None,
    chat_id: str | None,
    before: float | None,
    after: float | None,
) -> list[dict]:
    """Search via FTS5 trigram MATCH with optional filters."""
    rows: list[dict] = []

    fts_where = "corpus_messages_fts MATCH ?"
    fts_params: list[Any] = [query]

    clauses = [fts_where]
    params: list[Any] = list(fts_params)

    if group_id:
        clauses.append("c.group_id = ?")
        params.append(group_id)
    if chat_id:
        clauses.append("c.chat_id = ?")
        params.append(chat_id)
    if before is not None:
        clauses.append("c.created_at < ?")
        params.append(before)
    if after is not None:
        clauses.append("c.created_at > ?")
        params.append(after)

    where = " AND ".join(clauses)
    sql = (
        "SELECT c.message_id, c.sender_name, c.created_at, c.group_id, "
        "c.chat_id, c.chat_type, c.content_readable, c.recalled "
        "FROM corpus_messages c "
        "JOIN corpus_messages_fts f ON c.id = f.rowid "
        f"WHERE {where} "
        "ORDER BY c.created_at DESC "
        "LIMIT ?"
    )
    params.append(limit)

    try:
        cur = db.execute(sql, params)
        for row in cur:
            mid = str(row[0]) if row[0] else ""
            preview = _truncate_preview(row[6] or "")
            recalled = bool(row[7])
            if recalled:
                preview = f"[已撤回] {preview}"
            rows.append({
                "message_id": mid,
                "mid": f"[mid:{mid}]",
                "cite": f"[reply:{mid}]",
                "sender_name": row[1] or "",
                "created_at": row[2],
                "group_id": row[3] or "",
                "chat_id": row[4] or "",
                "chat_type": row[5] or "",
                "preview": preview,
                "recalled": recalled,
            })
    except sqlite3.OperationalError as exc:
        logger.warning("FTS search failed for query %r: %s", query, exc)
        return []
    return rows


def _like_search(
    db: sqlite3.Connection,
    query: str,
    limit: int,
    group_id: str | None,
    chat_id: str | None,
    before: float | None,
    after: float | None,
) -> list[dict]:
    """Fallback LIKE-based search for short CJK queries.

    Searches content_readable and sender_name with ``%term%`` for each
    CJK term in the query. Returns up to *limit* results.
    """
    rows: list[dict] = []

    # Split into individual CJK terms (treat each CJK char or run as a term)
    terms: list[str] = []
    current = ""
    for ch in query:
        if _CJK_RE.match(ch):
            current += ch
        else:
            if current:
                terms.append(current)
                current = ""
    if current:
        terms.append(current)

    # Also include the whole query as a single term (for non-CJK content)
    if not terms:
        terms = [query]

    # Build LIKE clauses: (content_readable LIKE ? OR sender_name LIKE ?) AND ...
    clauses: list[str] = []
    params: list[Any] = []
    for term in terms:
        pattern = f"%{term}%"
        clauses.append("(c.content_readable LIKE ? OR c.sender_name LIKE ?)")
        params.extend([pattern, pattern])

    if group_id:
        clauses.append("c.group_id = ?")
        params.append(group_id)
    if chat_id:
        clauses.append("c.chat_id = ?")
        params.append(chat_id)
    if before is not None:
        clauses.append("c.created_at < ?")
        params.append(before)
    if after is not None:
        clauses.append("c.created_at > ?")
        params.append(after)

    where = " AND ".join(clauses)
    sql = (
        "SELECT c.message_id, c.sender_name, c.created_at, c.group_id, "
        "c.chat_id, c.chat_type, c.content_readable, c.recalled "
        "FROM corpus_messages c "
        f"WHERE {where} "
        "ORDER BY c.created_at DESC "
        "LIMIT ?"
    )
    params.append(limit)

    try:
        cur = db.execute(sql, params)
        for row in cur:
            mid = str(row[0]) if row[0] else ""
            preview = _truncate_preview(row[6] or "")
            recalled = bool(row[7])
            if recalled:
                preview = f"[已撤回] {preview}"
            rows.append({
                "message_id": mid,
                "mid": f"[mid:{mid}]",
                "cite": f"[reply:{mid}]",
                "sender_name": row[1] or "",
                "created_at": row[2],
                "group_id": row[3] or "",
                "chat_id": row[4] or "",
                "chat_type": row[5] or "",
                "preview": preview,
                "recalled": recalled,
            })
    except sqlite3.OperationalError as exc:
        logger.warning("LIKE search failed for query %r: %s", query, exc)
        return []
    return rows


def _truncate_preview(text: str, max_chars: int = _MAX_PREVIEW_CHARS) -> str:
    """Return a snippet truncated at *max_chars* characters."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


# ── Public search API ─────────────────────────────────────────────────────


def _ensure_recalled_column(db: sqlite3.Connection) -> None:
    """Idempotently add the ``recalled`` column (added in v0.14.11).

    Search queries reference ``c.recalled``; without this, a search before
    any group_recall event would fail with ``no such column``.
    """
    try:
        cols = {r[1] for r in db.execute("PRAGMA table_info(corpus_messages)")}
        if "recalled" not in cols:
            db.execute("ALTER TABLE corpus_messages ADD COLUMN recalled INTEGER DEFAULT 0")
    except sqlite3.Error:
        pass


def search_corpus(
    query: str,
    *,
    db_path: str | Path | None = None,
    db: sqlite3.Connection | None = None,
    limit: int = _DEFAULT_TOOL_LIMIT,
    group_id: str | None = None,
    chat_id: str | None = None,
    before: float | None = None,
    after: float | None = None,
) -> dict:
    """Search corpus_messages and return compact ``[mid:...]`` results.

    Strategy:
    1. If the query contains CJK characters and fewer than 3 CJK codepoints,
       use LIKE fallback (trigram tokenisation is poor for 1-2 char queries).
    2. Otherwise, use FTS5 MATCH.
    3. If FTS is unavailable (no virtual table), degrade to LIKE search
       with a degradation notice.

    Args:
        query: Search query text (stripped, truncated to 200 chars).
        db_path: Path to state.db (default from hermes_constants).
        db: An open sqlite3.Connection (takes precedence over db_path).
        limit: Max results (hard-clamped to 100).
        group_id: Optional group filter.
        chat_id: Optional chat filter.
        before: Optional upper bound on created_at (Unix timestamp).
        after: Optional lower bound on created_at (Unix timestamp).

    Returns:
        A dict with ``success``, ``query``, ``count``, ``results``,
        ``notice``, and optional error/degradation fields.
    """
    # Sanitise inputs
    query = _sanitise_query(query)
    if not query:
        return {"success": False, "error": "Query is empty after sanitisation"}
    limit = max(1, min(limit, _MAX_DB_LIMIT))

    own_db = False
    if db is None:
        path = str(db_path or get_state_db_path())
        db = sqlite3.connect(path, timeout=10)
        db.execute("PRAGMA busy_timeout=30000")
        own_db = True
    else:
        path = ""

    try:
        _ensure_recalled_column(db)
        fts_ok = _fts_available_for_cached(db)

        cjk_count = _count_cjk(query)
        use_like = cjk_count > 0 and cjk_count < 3
        notice: str | None = None

        if use_like:
            notice = (
                f"Query '{query}' has only {cjk_count} CJK character(s) — "
                "using LIKE fallback (trigram FTS needs >= 3 characters for "
                "reliable CJK matching)."
            )
            results = _like_search(db, query, limit, group_id, chat_id, before, after)
        elif fts_ok:
            results = _fts_search(db, query, limit, group_id, chat_id, before, after)
            if not results and cjk_count > 0:
                # FTS returned nothing for CJK query — try LIKE as fallback
                notice = (
                    f"No FTS results for '{query}' (CJK={cjk_count} chars) — "
                    "trying LIKE fallback."
                )
                results = _like_search(db, query, limit, group_id, chat_id, before, after)
        else:
            notice = (
                "corpus_messages_fts not available — using LIKE fallback. "
                "Run rebuild_fts() for full-text search."
            )
            results = _like_search(db, query, limit, group_id, chat_id, before, after)

        response: dict[str, Any] = {
            "success": True,
            "query": query,
            "count": len(results),
            "results": results,
        }
        if notice:
            response["notice"] = notice
        if not fts_ok:
            response["degraded"] = True
        return response
    except sqlite3.OperationalError as exc:
        return {
            "success": False,
            "error": f"SQLite error: {exc}",
            "query": query,
            "count": 0,
            "results": [],
        }
    finally:
        if own_db:
            db.close()


def _fts_available_for_cached(db: sqlite3.Connection) -> bool:
    """Check FTS availability on an already-open connection (lightweight)."""
    try:
        cur = db.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='corpus_messages_fts'"
        )
        return cur.fetchone() is not None
    except sqlite3.OperationalError:
        return False
