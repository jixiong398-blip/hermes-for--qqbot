"""Gate 3 search hardening: bounded sanitizer and canonical LIKE fallback."""

from hermes_state import MAX_FTS5_QUERY_CHARS, SessionDB


def _drop_derived_index(db: SessionDB, table: str) -> None:
    with db._lock:
        db._conn.execute(f"DROP TABLE IF EXISTS {table}")
        db._conn.commit()


def test_fts_sanitizer_caps_input_and_strips_extended_match_grammar():
    raw = "x" * (MAX_FTS5_QUERY_CHARS + 500) + ":/#&|~[]<>,;!?$=50%"
    sanitized = SessionDB._sanitize_fts5_query(raw)

    assert len(sanitized) <= MAX_FTS5_QUERY_CHARS
    for special in (":", "/", "#", "&", "|", "~", "[", "]", "<", ">", ",", ";", "!", "?", "$", "="):
        assert special not in sanitized
    assert "%" not in sanitized


def test_canonical_like_fallback_recovers_when_unicode_fts_is_missing(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("s1", source="cli")
        db.append_message("s1", "user", "canonical-token survives index loss")
        _drop_derived_index(db, "messages_fts")

        result = db.search_messages("canonical-token")

        assert len(result) == 1
        assert result[0]["session_id"] == "s1"
    finally:
        db.close()


def test_canonical_like_fallback_preserves_not_boolean_and_filters(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("s1", source="cli")
        db.create_session("s2", source="telegram")
        db.append_message("s1", "user", "alpha only")
        db.append_message("s2", "user", "alpha beta")
        _drop_derived_index(db, "messages_fts")

        result = db.search_messages(
            "alpha NOT beta",
            source_filter=["cli"],
        )

        assert len(result) == 1
        assert result[0]["session_id"] == "s1"
    finally:
        db.close()


def test_cjk_trigram_failure_falls_back_without_losing_canonical_message(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        db.create_session("s1", source="cli")
        db.append_message("s1", "user", "记忆系统的持久化检查")
        _drop_derived_index(db, "messages_fts_trigram")

        result = db.search_messages("记忆系统")

        assert len(result) == 1
        assert result[0]["session_id"] == "s1"
    finally:
        db.close()
