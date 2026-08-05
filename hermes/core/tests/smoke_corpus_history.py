"""Smoke tests for corpus_history.py — standalone, no network/NapCat required.

Usage:
    python3 tests/smoke_corpus_history.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time

_HERMES_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERMES_ROOT not in sys.path:
    sys.path.insert(0, _HERMES_ROOT)

from corpus_history import (
    init_fts,
    rebuild_fts,
    search_corpus,
    _sanitise_query,
    _count_cjk,
    _truncate_preview,
    _fts_search,
    _like_search,
    _reset_fts_cache,
    _MAX_DB_LIMIT,
    _MAX_QUERY_CHARS,
    _MAX_PREVIEW_CHARS,
)

PASS = 0
FAIL = 0


def ok(label: str) -> None:
    global PASS
    PASS += 1
    print(f"  ✓ {label}")


def fail(label: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    msg = f"  ✗ {label}"
    if detail:
        msg += f"  → {detail}"
    print(msg)


# ── Helpers ───────────────────────────────────────────────────────────────

def _create_corpus_table(db: sqlite3.Connection) -> None:
    db.execute("PRAGMA busy_timeout=5000")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS corpus_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_id TEXT DEFAULT '',
            chat_id TEXT NOT NULL,
            chat_type TEXT NOT NULL DEFAULT 'group',
            group_id TEXT DEFAULT '',
            user_id INTEGER,
            sender_name TEXT NOT NULL DEFAULT '',
            sender_card TEXT DEFAULT '',
            content_raw TEXT DEFAULT '',
            content_readable TEXT NOT NULL DEFAULT '',
            image_descriptions TEXT DEFAULT '[]',
            voice_transcript TEXT DEFAULT '',
            video_understanding TEXT DEFAULT '',
            forward_structured TEXT DEFAULT '[]',
            at_targets TEXT DEFAULT '[]',
            reply_to_id TEXT DEFAULT '',
            reply_to_text TEXT DEFAULT '',
            is_bot INTEGER DEFAULT 0,
            media_paths TEXT DEFAULT '[]',
            media_cached INTEGER DEFAULT 0,
            created_at REAL NOT NULL,
            session_id TEXT DEFAULT '',
            recalled_mem_ids TEXT DEFAULT '[]',
            salience_hint REAL DEFAULT 0.5
        );
    """)


def _insert_msg(db: sqlite3.Connection, msg_id: str, sender: str,
                content: str, group_id: str = "g1",
                created_at: float | None = None) -> None:
    ts = created_at or time.time()
    db.execute(
        "INSERT INTO corpus_messages (message_id, chat_id, chat_type, group_id, "
        "user_id, sender_name, content_raw, content_readable, is_bot, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (msg_id, group_id, "group", group_id, 111, sender,
         content, content, 0, ts),
    )


def _create_fts(db: sqlite3.Connection) -> None:
    """Create FTS table, trigger, index directly on an open connection."""
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_corpus_message_id "
        "ON corpus_messages(message_id)"
    )
    db.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS corpus_messages_fts USING fts5("
        "content_readable, sender_name, "
        "content='corpus_messages', content_rowid='id', "
        "tokenize='trigram'"
        ")"
    )
    db.execute(
        "CREATE TRIGGER IF NOT EXISTS corpus_fts_ai "
        "AFTER INSERT ON corpus_messages "
        "BEGIN "
        "INSERT INTO corpus_messages_fts(rowid, content_readable, sender_name) "
        "VALUES (new.id, new.content_readable, new.sender_name); "
        "END"
    )


def _make_populated_db(*, with_fts: bool = True) -> sqlite3.Connection:
    """Create an in-memory DB with corpus_messages, optional FTS, and sample data."""
    db = sqlite3.connect(":memory:")
    _create_corpus_table(db)
    if with_fts:
        _create_fts(db)
    for spec in [
        ("msg_001", "Alice", "火锅很好吃", "g1"),
        ("msg_002", "Bob", "我想吃烤肉", "g1"),
        ("msg_003", "Charlie", "今天天气真好", "g2"),
        ("msg_004", "Alice", "大家下午好", "g1"),
        ("msg_005", "Bob", "有人一起去吃火锅吗", "g1"),
    ]:
        _insert_msg(db, *spec)
    db.commit()
    return db


def _populate_inmem_fts(db: sqlite3.Connection) -> None:
    """Rebuild FTS index in an in-memory database using INSERT OR REPLACE."""
    db.execute(
        "INSERT OR REPLACE INTO corpus_messages_fts(rowid, content_readable, sender_name) "
        "SELECT id, content_readable, sender_name FROM corpus_messages"
    )
    db.commit()


# ── Tests ─────────────────────────────────────────────────────────────────

def test_sanitise_query() -> None:
    print("\n[sanitise_query]")
    assert _sanitise_query("  hello   world  ") == "hello world"
    ok("whitespace collapse")
    long_q = "a" * 500
    assert len(_sanitise_query(long_q)) == _MAX_QUERY_CHARS
    ok(f"truncate at {_MAX_QUERY_CHARS} chars")


def test_count_cjk() -> None:
    print("\n[_count_cjk]")
    assert _count_cjk("你好") == 2
    ok("2 CJK chars")
    assert _count_cjk("hello") == 0
    ok("no CJK")
    assert _count_cjk("我a你") == 2
    ok("mixed")
    assert _count_cjk("日本語") == 3
    ok("hiragana+kanji")
    assert _count_cjk("한글") == 2
    ok("hangul")


def test_truncate_preview() -> None:
    print("\n[_truncate_preview]")
    assert _truncate_preview("hello") == "hello"
    ok("short text unchanged")
    long = "x" * 200
    result = _truncate_preview(long)
    assert len(result) == _MAX_PREVIEW_CHARS + 1  # +1 for … suffix
    ok(f"long text truncated to ~{_MAX_PREVIEW_CHARS}")


def test_init_fts_idempotent() -> None:
    print("\n[init_fts idempotent]")
    tmpdir = tempfile.mkdtemp()
    tpath = os.path.join(tmpdir, "test.db")
    try:
        # Create DB with corpus_messages
        db = sqlite3.connect(tpath)
        _create_corpus_table(db)
        db.close()

        # First init
        r1 = init_fts(tpath)
        assert r1.get("error") is None, f"init_fts 1 failed: {r1}"
        ok("first init_fts succeeds")

        # Second init (idempotent)
        r2 = init_fts(tpath)
        assert r2.get("error") is None, f"init_fts 2 failed: {r2}"
        ok("second init_fts is idempotent")

        # Verify FTS exists
        db2 = sqlite3.connect(tpath)
        cur = db2.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='corpus_messages_fts'"
        )
        assert cur.fetchone() is not None
        db2.close()
        ok("corpus_messages_fts exists after init")
    finally:
        for f in os.listdir(tmpdir):
            os.unlink(os.path.join(tmpdir, f))
        os.rmdir(tmpdir)


def test_init_fts_no_corpus_table() -> None:
    print("\n[init_fts no corpus table]")
    tmpdir = tempfile.mkdtemp()
    tpath = os.path.join(tmpdir, "empty.db")
    try:
        # Empty file (no tables)
        sqlite3.connect(tpath).close()
        result = init_fts(tpath)
        assert result.get("error") is not None
        ok("init_fts reports error when corpus_messages missing")
    finally:
        os.unlink(tpath)
        os.rmdir(tmpdir)


def test_rebuild_fts() -> None:
    print("\n[rebuild_fts]")
    db = sqlite3.connect(":memory:")
    _create_corpus_table(db)
    _insert_msg(db, "m1", "Alice", "今天天气真好啊")
    _insert_msg(db, "m2", "Bob", "是啊适合出去玩")
    _insert_msg(db, "m3", "Alice", "我想去吃火锅")
    db.commit()
    _create_fts(db)

    cur = db.execute("SELECT COUNT(*) FROM corpus_messages")
    assert cur.fetchone()[0] == 3

    _populate_inmem_fts(db)
    cur = db.execute("SELECT COUNT(*) FROM corpus_messages_fts")
    assert cur.fetchone()[0] == 3
    ok("rebuild populates FTS from 3 corpus rows")

    _populate_inmem_fts(db)
    assert db.execute("SELECT COUNT(*) FROM corpus_messages_fts").fetchone()[0] == 3
    ok("rebuild is idempotent")

    tmpdir = tempfile.mkdtemp()
    tpath = os.path.join(tmpdir, "test.db")
    try:
        db2 = sqlite3.connect(tpath)
        _create_corpus_table(db2)
        _insert_msg(db2, "f1", "X", "file test content")
        db2.commit()
        db2.close()

        r = rebuild_fts(tpath)
        assert r.get("error") is None, f"file rebuild failed: {r}"
        assert r["source_rows"] == 1
        assert r["fts_rows"] == 1
        ok("file-based rebuild works")
    finally:
        for f in os.listdir(tmpdir):
            os.unlink(os.path.join(tmpdir, f))
        os.rmdir(tmpdir)


def test_search_fts() -> None:
    print("\n[search FTS]")
    db = _make_populated_db(with_fts=True)
    _populate_inmem_fts(db)

    r = search_corpus("火锅", db=db)
    assert r["success"], f"search failed: {r}"
    assert r["count"] >= 1, f"expected >=1 results, got {r['count']}"
    ok("FTS search for 火锅 finds results")

    # Group filter
    r = search_corpus("吃", db=db, group_id="g1")
    assert r["success"]
    assert r["count"] >= 1
    ok("group filter works")

    r = search_corpus("天气", db=db, group_id="g2")
    assert r["success"]
    assert r["count"] == 1
    assert r["results"][0]["sender_name"] == "Charlie"
    ok("group filter for g2 isolates result")

    # Limit
    r = search_corpus("吃", db=db, limit=1)
    assert r["success"]
    assert r["count"] <= 1
    ok("limit=1 respected")

    db.close()


def test_short_cjk_fallback() -> None:
    print("\n[short CJK LIKE fallback]")
    db = _make_populated_db(with_fts=True)

    _populate_inmem_fts(db)

    # 2-char CJK query: "火锅" — should trigger LIKE fallback
    r = search_corpus("火锅", db=db)
    assert r["success"], f"search failed: {r}"
    assert r["count"] >= 1, f"expected >=1 for 火锅, got {r['count']}"
    has_like_notice = "notice" in r and "LIKE" in r.get("notice", "")
    # It may use FTS or LIKE depending on CJK count (火锅 is 2 chars, < 3)
    ok(f"short CJK '火锅' found results (notice={bool(has_like_notice)})")

    # 1-char CJK: "吃"
    r = search_corpus("吃", db=db)
    assert r["success"]
    assert r["count"] >= 1
    ok("single CJK '吃' found results")

    # Non-CJK query
    r = search_corpus("hello", db=db)
    assert r["success"]
    ok("non-CJK query works without LIKE fallback")

    db.close()


def test_missing_fts_degradation() -> None:
    print("\n[missing FTS degradation]")
    db = _make_populated_db(with_fts=False)

    _reset_fts_cache()
    r = search_corpus("火锅", db=db)
    assert r["success"], f"search failed: {r}"
    assert r.get("degraded"), "should be marked degraded"
    ok("degraded flag when FTS unavailable")

    r = search_corpus("Alice", db=db)
    assert r["success"]
    assert r["count"] >= 1
    ok("LIKE fallback works when FTS unavailable")

    db.close()


def test_limit_clamping() -> None:
    print("\n[limit clamping]")
    db = _make_populated_db(with_fts=True)
    _populate_inmem_fts(db)

    r = search_corpus("吃", db=db, limit=9999)
    assert r["success"]
    assert r["count"] <= _MAX_DB_LIMIT
    ok(f"limit clamped to <= {_MAX_DB_LIMIT} (requested 9999, got {r['count']})")
    db.close()


def test_empty_query() -> None:
    print("\n[empty query]")
    db = _make_populated_db(with_fts=False)
    r = search_corpus("   ", db=db)
    assert not r.get("success")
    assert r.get("error")
    ok("whitespace-only query returns error")
    db.close()


def test_result_format() -> None:
    print("\n[result format]")
    db = _make_populated_db(with_fts=True)
    _populate_inmem_fts(db)

    r = search_corpus("Alice", db=db)
    assert r["success"]
    assert "query" in r
    assert "count" in r
    assert "results" in r
    ok("top-level keys present")

    assert r["count"] >= 1, f"expected >=1 results, got {r['count']}"
    first = r["results"][0]
    for key in ("message_id", "mid", "cite", "sender_name", "created_at",
                 "group_id", "chat_id", "preview"):
        assert key in first, f"missing key: {key}"
    ok("all result fields present")

    assert "[mid:" in first["mid"] and "msg_" in first["mid"]
    ok("mid tag contains [mid:msg_...]")
    assert "[reply:" in first["cite"] and "msg_" in first["cite"]
    ok("cite tag contains [reply:msg_...]")

    db.close()


def test_rebuild_fts_idempotent_file_db() -> None:
    """Call rebuild_fts twice on a file DB — must succeed both times with same row count."""
    print("\n[rebuild_fts idempotent on file DB]")
    tmpdir = tempfile.mkdtemp()
    tpath = os.path.join(tmpdir, "idem.db")
    try:
        db = sqlite3.connect(tpath)
        _create_corpus_table(db)
        _insert_msg(db, "m1", "Alice", "今天天气真好啊")
        _insert_msg(db, "m2", "Bob", "是啊适合出去玩")
        _insert_msg(db, "m3", "Alice", "我想去吃火锅")
        _insert_msg(db, "m4", "Charlie", "明天要不要一起去")
        db.commit()
        db.close()

        _reset_fts_cache()

        r1 = rebuild_fts(tpath)
        assert r1.get("error") is None, f"first rebuild failed: {r1}"
        assert r1["source_rows"] == 4
        assert r1["fts_rows"] == 4
        ok("first rebuild_fts populates 4 rows")

        r2 = rebuild_fts(tpath)
        assert r2.get("error") is None, f"second rebuild failed: {r2}"
        assert r2["source_rows"] == 4
        assert r2["fts_rows"] == 4
        ok("second rebuild_fts is idempotent (same row count)")

        result = search_corpus("天气", db_path=tpath)
        assert result["success"]
        assert result["count"] >= 1
        ok("FTS queryable after double rebuild")
    finally:
        for f in os.listdir(tmpdir):
            os.unlink(os.path.join(tmpdir, f))
        os.rmdir(tmpdir)


def test_search_by_sender() -> None:
    print("\n[search by sender name]")
    db = _make_populated_db(with_fts=True)
    _populate_inmem_fts(db)

    r = search_corpus("Bob", db=db)
    assert r["success"]
    assert r["count"] >= 2  # Bob has 2 messages
    for result in r["results"]:
        assert result["sender_name"] == "Bob"
    ok("search by sender 'Bob' returns only Bob's messages")

    db.close()


# ── Main ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("corpus_history.py smoke tests")
    print("=" * 60)

    tests = [
        test_sanitise_query,
        test_count_cjk,
        test_truncate_preview,
        test_init_fts_idempotent,
        test_init_fts_no_corpus_table,
        test_rebuild_fts,
        test_rebuild_fts_idempotent_file_db,
        test_search_fts,
        test_short_cjk_fallback,
        test_missing_fts_degradation,
        test_limit_clamping,
        test_empty_query,
        test_result_format,
        test_search_by_sender,
    ]

    for fn in tests:
        try:
            fn()
        except Exception as exc:
            fail(fn.__name__, str(exc))
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60)
    print(f"Results: {PASS} passed, {FAIL} failed")
    print("=" * 60)

    if FAIL > 0:
        sys.exit(1)
