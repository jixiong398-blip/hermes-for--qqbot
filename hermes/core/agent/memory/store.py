r"""
                   _                                   _
                  | |                                 | |
   _____  ___  ___| |_   _____  ___  ___  ___  ___  ___| |_
  / _ \ \/ _ \/ __| __| / _ \ \/ / / __|/ __|/ __|/ __| __|
 |  __/>  __>\\__ \ |_ |  __/>  <  \__ \ (__| (__| (__| |_
  \___/_/\___|___/\__| \___/_/\_\ |___/\___|\___|\___|\__|

         ┌─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┐
         │      清   尘   璃   落      │
         └─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┘
    上联：代码永无 bug  佛祖座下莲花放
    下联：清尘璃落赐福  素世心中万世安

              _ooOoo_
             o8888888o
             88" . "88
             (| -_- |)
             O\  =  /O
          ____/`---'\____
        .'  \\|     |//  `.
       /  \\|||  :  |||//  \
      /  _||||| -:- |||||-  \
      |   | \\\  -  /// |   |
      | \_|  ''\---/''  |   |
      \  .-\__  `-`  ___/-. /
    ___`. .'  /--.--\  `. . __
  ."" '<  `.___\_<|>_/___.'  >'"".
 | | :  `- \`.;`\ _ /`;.`/ - ` : | |
 \  \ `-.   \_ __\ /__ _/   .-` /  /
======`-.____`-.___\_____/___.-`____.-'======
                   `=---='
         }  }  }  }  莲花台  {  {  {  {
         }  }  }  }  莲花台  {  {  {  {
         }  }  }  }  莲花台  {  {  {  {
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
              佛祖保佑    永无 bug

Unified Memory Store — SQLite backend for all memory subsystems.

Tables:
  - short_term_entries: session-scoped topic/message tracking
  - long_term_entries: persistent facts with categories, tags, confidence
  - workflow_entries: procedural patterns with usage-based decay
  - wiki_entries: Karpathy LLM Wiki knowledge chunks
  - consolidation_log: audit trail of STM->LTM/WFM promotions
  - corpus_messages: permanent training corpus (never pruned)
  - groups_registry: minimal group registration
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home, get_memory_db_path

logger = logging.getLogger(__name__)


def _memory_db_path() -> Path:
    return get_memory_db_path()


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS short_term_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    role TEXT NOT NULL,
    speaker_name TEXT DEFAULT '',
    chat_type TEXT DEFAULT 'dm',
    content TEXT NOT NULL,
    topics TEXT DEFAULT '[]',
    intent TEXT DEFAULT '',
    emotional_tone TEXT DEFAULT '',
    created_at REAL NOT NULL,
    summarized INTEGER DEFAULT 0,
    bot_replied INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS long_term_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL DEFAULT 'general',
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    tags TEXT DEFAULT '[]',
    confidence REAL DEFAULT 0.5,
    source_session_ids TEXT DEFAULT '[]',
    retrieval_count INTEGER DEFAULT 0,
    last_retrieved REAL DEFAULT 0.0,
    ttl_days INTEGER DEFAULT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    memory_type TEXT NOT NULL DEFAULT 'semantic',
    type_data TEXT DEFAULT '{}',
    salience REAL DEFAULT 0.5,
    recall_strength REAL DEFAULT 1.0,
    reconsolidation_count INTEGER DEFAULT 0,
    last_recalled_at TEXT,
    source_user_id TEXT DEFAULT '',
    source_message_ts TEXT DEFAULT '',
    source_context TEXT DEFAULT '',
    derivation TEXT DEFAULT 'direct',
    supersedes_id INTEGER,
    active INTEGER DEFAULT 1,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS workflow_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL,
    trigger_patterns TEXT DEFAULT '[]',
    steps TEXT DEFAULT '[]',
    preconditions TEXT DEFAULT '[]',
    expected_outcome TEXT DEFAULT '',
    usage_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    base_weight REAL DEFAULT 1.0,
    current_weight REAL DEFAULT 1.0,
    last_used REAL DEFAULT 0.0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    source_session_ids TEXT DEFAULT '[]',
    version INTEGER DEFAULT 1,
    skill_md_path TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS wiki_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url TEXT NOT NULL,
    title TEXT NOT NULL,
    section TEXT DEFAULT '',
    content TEXT NOT NULL,
    chunk_index INTEGER DEFAULT 0,
    tags TEXT DEFAULT '[]',
    embedding_hash TEXT DEFAULT '',
    retrieval_count INTEGER DEFAULT 0,
    last_retrieved REAL DEFAULT 0.0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(source_url, chunk_index)
);

CREATE TABLE IF NOT EXISTS consolidation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    source_id INTEGER,
    target_id INTEGER,
    action TEXT NOT NULL,
    detail TEXT DEFAULT '',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_message_buffer (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id TEXT NOT NULL,
    chat_type TEXT NOT NULL DEFAULT 'group',
    user_id INTEGER,
    sender_name TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    is_bot INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    message_id TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS memory_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    src_id INTEGER NOT NULL,
    dst_id INTEGER NOT NULL,
    relation TEXT NOT NULL,
    weight REAL DEFAULT 0.5,
    created_at REAL NOT NULL,
    UNIQUE(src_id, dst_id, relation),
    FOREIGN KEY (src_id) REFERENCES long_term_entries(id),
    FOREIGN KEY (dst_id) REFERENCES long_term_entries(id)
);

CREATE TABLE IF NOT EXISTS _sleep_watermark (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS _memory_type_registry (
    type_name TEXT PRIMARY KEY,
    type_data_schema TEXT,
    description TEXT,
    created_at TEXT,
    created_by TEXT DEFAULT 'system'
);

CREATE TABLE IF NOT EXISTS _relation_registry (
    relation TEXT PRIMARY KEY,
    description TEXT,
    is_traversable INTEGER DEFAULT 1,
    created_by TEXT DEFAULT 'system'
);

CREATE INDEX IF NOT EXISTS idx_ste_session ON short_term_entries(session_id, turn_index);
CREATE INDEX IF NOT EXISTS idx_ste_created ON short_term_entries(created_at);
CREATE INDEX IF NOT EXISTS idx_lte_category ON long_term_entries(category);
CREATE INDEX IF NOT EXISTS idx_lte_retrieval ON long_term_entries(retrieval_count);
CREATE INDEX IF NOT EXISTS idx_lte_confidence ON long_term_entries(confidence);
CREATE INDEX IF NOT EXISTS idx_wfe_weight ON workflow_entries(current_weight);
CREATE INDEX IF NOT EXISTS idx_wfe_usage ON workflow_entries(usage_count);
CREATE INDEX IF NOT EXISTS idx_wfe_last_used ON workflow_entries(last_used);
CREATE INDEX IF NOT EXISTS idx_we_title ON wiki_entries(title);
CREATE INDEX IF NOT EXISTS idx_we_source ON wiki_entries(source_url);

CREATE INDEX IF NOT EXISTS idx_cmb_chat_time ON chat_message_buffer(chat_id, created_at);

-- FTS5 for full-text search
CREATE VIRTUAL TABLE IF NOT EXISTS lte_fts USING fts5(
    key, value, tags,
    content='long_term_entries',
    content_rowid='id'
);

CREATE VIRTUAL TABLE IF NOT EXISTS wfe_fts USING fts5(
    name, description, trigger_patterns, steps,
    content='workflow_entries',
    content_rowid='id'
);

CREATE VIRTUAL TABLE IF NOT EXISTS we_fts USING fts5(
    title, section, content, tags,
    content='wiki_entries',
    content_rowid='id'
);

-- FTS5 triggers to keep external content indexes in sync
CREATE TRIGGER IF NOT EXISTS lte_fts_ai AFTER INSERT ON long_term_entries BEGIN
    INSERT INTO lte_fts(rowid, key, value, tags) VALUES (new.id, new.key, new.value, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS lte_fts_ad AFTER DELETE ON long_term_entries BEGIN
    INSERT INTO lte_fts(lte_fts, rowid, key, value, tags) VALUES('delete', old.id, old.key, old.value, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS lte_fts_au AFTER UPDATE ON long_term_entries BEGIN
    INSERT INTO lte_fts(lte_fts, rowid, key, value, tags) VALUES('delete', old.id, old.key, old.value, old.tags);
    INSERT INTO lte_fts(rowid, key, value, tags) VALUES (new.id, new.key, new.value, new.tags);
END;

CREATE TRIGGER IF NOT EXISTS wfe_fts_ai AFTER INSERT ON workflow_entries BEGIN
    INSERT INTO wfe_fts(rowid, name, description, trigger_patterns, steps) VALUES (new.id, new.name, new.description, new.trigger_patterns, new.steps);
END;
CREATE TRIGGER IF NOT EXISTS wfe_fts_ad AFTER DELETE ON workflow_entries BEGIN
    INSERT INTO wfe_fts(wfe_fts, rowid, name, description, trigger_patterns, steps) VALUES('delete', old.id, old.name, old.description, old.trigger_patterns, old.steps);
END;
CREATE TRIGGER IF NOT EXISTS wfe_fts_au AFTER UPDATE ON workflow_entries BEGIN
    INSERT INTO wfe_fts(wfe_fts, rowid, name, description, trigger_patterns, steps) VALUES('delete', old.id, old.name, old.description, old.trigger_patterns, old.steps);
    INSERT INTO wfe_fts(rowid, name, description, trigger_patterns, steps) VALUES (new.id, new.name, new.description, new.trigger_patterns, new.steps);
END;

CREATE TRIGGER IF NOT EXISTS we_fts_ai AFTER INSERT ON wiki_entries BEGIN
    INSERT INTO we_fts(rowid, title, section, content, tags) VALUES (new.id, new.title, new.section, new.content, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS we_fts_ad AFTER DELETE ON wiki_entries BEGIN
    INSERT INTO we_fts(we_fts, rowid, title, section, content, tags) VALUES('delete', old.id, old.title, old.section, old.content, old.tags);
END;
CREATE TRIGGER IF NOT EXISTS we_fts_au AFTER UPDATE ON wiki_entries BEGIN
    INSERT INTO we_fts(we_fts, rowid, title, section, content, tags) VALUES('delete', old.id, old.title, old.section, old.content, old.tags);
    INSERT INTO we_fts(rowid, title, section, content, tags) VALUES (new.id, new.title, new.section, new.content, new.tags);
END;

-- ── 核心记忆表（soul 的自传体记忆，不衰减，每次全量加载） ──
CREATE TABLE IF NOT EXISTS core_memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category    TEXT NOT NULL DEFAULT 'general',
    content     TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'migrated',
    occurred_at REAL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    active      INTEGER DEFAULT 1,
    deleted_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_cm_category ON core_memories(category);
CREATE INDEX IF NOT EXISTS idx_cm_active_time ON core_memories(active, occurred_at);
"""


# These columns were introduced by the local memory v2 work after the original
# v1 store was shipped.  Keep the list fixed and code-owned: existing memory
# databases receive only these additive columns during normal initialization.
# Table rewrites (notably removing the legacy table-level UNIQUE constraint)
# remain an explicit migration step and are never performed implicitly here.
_LTM_COMPAT_COLUMNS = (
    ("ttl_days", "INTEGER DEFAULT NULL"),
    ("memory_type", "TEXT NOT NULL DEFAULT 'semantic'"),
    ("type_data", "TEXT DEFAULT '{}'"),
    ("salience", "REAL DEFAULT 0.5"),
    ("recall_strength", "REAL DEFAULT 1.0"),
    ("reconsolidation_count", "INTEGER DEFAULT 0"),
    ("last_recalled_at", "TEXT"),
    ("source_user_id", "TEXT DEFAULT ''"),
    ("source_message_ts", "TEXT DEFAULT ''"),
    ("source_context", "TEXT DEFAULT ''"),
    ("derivation", "TEXT DEFAULT 'direct'"),
    ("supersedes_id", "INTEGER"),
    ("active", "INTEGER DEFAULT 1"),
    ("deleted_at", "TEXT"),
)


@dataclass
class ShortTermEntry:
    id: int = 0
    session_id: str = ""
    turn_index: int = 0
    role: str = "user"
    speaker_name: str = ""
    chat_type: str = "dm"
    content: str = ""
    topics: List[str] = field(default_factory=list)
    intent: str = ""
    emotional_tone: str = ""
    created_at: float = 0.0
    summarized: bool = False
    bot_replied: bool = True


@dataclass
class LongTermEntry:
    id: int = 0
    category: str = "general"
    key: str = ""
    value: str = ""
    tags: List[str] = field(default_factory=list)
    confidence: float = 0.5
    source_session_ids: List[str] = field(default_factory=list)
    retrieval_count: int = 0
    last_retrieved: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0
    # v2 closed-loop fields follow the original v1 positional contract.
    ttl_days: Optional[int] = None
    memory_type: str = "semantic"
    type_data: Dict[str, Any] = field(default_factory=dict)
    salience: float = 0.5
    recall_strength: float = 1.0
    reconsolidation_count: int = 0
    last_recalled_at: Optional[str] = None
    source_user_id: str = ""
    source_message_ts: str = ""
    source_context: str = ""
    derivation: str = "direct"
    supersedes_id: Optional[int] = None
    active: bool = True
    deleted_at: Optional[str] = None


@dataclass
class WorkflowEntry:
    id: int = 0
    name: str = ""
    description: str = ""
    trigger_patterns: List[str] = field(default_factory=list)
    steps: List[str] = field(default_factory=list)
    preconditions: List[str] = field(default_factory=list)
    expected_outcome: str = ""
    usage_count: int = 0
    success_count: int = 0
    base_weight: float = 1.0
    current_weight: float = 1.0
    last_used: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0
    source_session_ids: List[str] = field(default_factory=list)
    version: int = 1
    skill_md_path: str = ""


@dataclass
class WikiEntry:
    id: int = 0
    source_url: str = ""
    title: str = ""
    section: str = ""
    content: str = ""
    chunk_index: int = 0
    tags: List[str] = field(default_factory=list)
    embedding_hash: str = ""
    retrieval_count: int = 0
    last_retrieved: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0


class MemoryStore:
    """Thread-safe SQLite store for all memory subsystems."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _memory_db_path()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._legacy_ltm_unique = False
        self._init_db()

    _all_conns = set()

    def _register_conn(self, conn) -> None:
        try:
            MemoryStore._all_conns.add(conn)
        except Exception:
            pass

    @staticmethod
    def _diagnose_conns() -> str:
        parts = []
        for c in list(MemoryStore._all_conns):
            try:
                parts.append(f"in_txn={c.in_transaction} total_changes={c.total_changes}")
            except Exception:
                parts.append("closed")
        return "; ".join(parts) if parts else "none"

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(str(self._db_path), timeout=30)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA busy_timeout=30000")
            self._register_conn(self._local.conn)
        return self._local.conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript(SCHEMA_SQL)

        # The shipped v1 database predates the closed-loop memory fields used
        # by LTM/EPI/L6 code.  Bring old files up to the additive contract
        # without rebuilding tables, dropping indexes, or rewriting rows.
        ltm_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(long_term_entries)")
        }
        ltm_schema_changed = False
        for column_name, column_definition in _LTM_COMPAT_COLUMNS:
            if column_name in ltm_columns:
                continue
            conn.execute(
                f"ALTER TABLE long_term_entries ADD COLUMN {column_name} "
                f"{column_definition}"
            )
            ltm_columns.add(column_name)
            ltm_schema_changed = True

        # These indexes are safe additive structures.  A legacy database may
        # still have the table-level UNIQUE(category, key); do not rewrite it
        # here because removing that constraint requires its own backup-gated
        # migration.  The partial index is therefore best-effort when legacy
        # rows already violate the active-key invariant.
        for index_sql in (
            "CREATE INDEX IF NOT EXISTS idx_ltm_type "
            "ON long_term_entries(memory_type) WHERE active=1",
            "CREATE INDEX IF NOT EXISTS idx_ltm_source_user "
            "ON long_term_entries(source_user_id) WHERE active=1",
            "CREATE INDEX IF NOT EXISTS idx_ltm_salience "
            "ON long_term_entries(salience)",
            "CREATE INDEX IF NOT EXISTS idx_ltm_recall "
            "ON long_term_entries(last_recalled_at)",
            "CREATE INDEX IF NOT EXISTS idx_ltm_active "
            "ON long_term_entries(active)",
            "CREATE INDEX IF NOT EXISTS idx_edges_src ON memory_edges(src_id)",
            "CREATE INDEX IF NOT EXISTS idx_edges_dst ON memory_edges(dst_id)",
        ):
            try:
                conn.execute(index_sql)
            except sqlite3.DatabaseError as error:
                # Keep initialization usable for a partially migrated legacy
                # file; the missing index is reported but never hides the
                # additive column/table migration result.
                logger.warning("Memory schema index skipped: %s", error)

        for type_name, description in (
            ("semantic", "Persistent facts/knowledge about users, world, self"),
            ("episodic", "Conversation segments used for abstraction"),
            ("procedural", "Learned behavior patterns and action templates"),
        ):
            conn.execute(
                "INSERT OR IGNORE INTO _memory_type_registry "
                "(type_name, description, created_at) VALUES (?, ?, ?)",
                (type_name, description, datetime.now(timezone.utc).isoformat()),
            )
        for relation, description, traversable in (
            ("related_to", "General association", 1),
            ("supports", "One memory supports another", 1),
            ("contradicts", "Conflicting memories", 1),
            ("abstracts_from", "Semantic fact abstracted from episodic cluster", 0),
            ("corrected_by", "New memory supersedes old via correction", 0),
        ):
            conn.execute(
                "INSERT OR IGNORE INTO _relation_registry "
                "(relation, description, is_traversable, created_by) "
                "VALUES (?, ?, ?, 'system')",
                (relation, description, traversable),
            )

        # Fresh schemas use a partial unique index so inactive correction
        # history can retain the original logical key.  An existing v1 table
        # may still carry a table-level UNIQUE(category, key); detect it and
        # use the compatibility rename in supersede_memory instead of doing a
        # destructive table rewrite during normal startup.
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_ltm_unique_active "
                "ON long_term_entries(category, key) WHERE active=1"
            )
        except sqlite3.IntegrityError as error:
            logger.warning(
                "Memory active-key index deferred for legacy duplicate rows: %s",
                error,
            )
        self._legacy_ltm_unique = self._detect_legacy_ltm_unique(conn)

        # Migration: add message_id column for replied-message lookup
        cols = {r[1] for r in conn.execute("PRAGMA table_info(chat_message_buffer)")}
        if "message_id" not in cols:
            conn.execute("ALTER TABLE chat_message_buffer ADD COLUMN message_id TEXT DEFAULT ''")
        chat_schema_changed = "message_id" not in cols
        conn.commit()
        if ltm_schema_changed or chat_schema_changed:
            # FTS5 external-content indexes are not automatically populated
            # when an old database gains columns/tables.  Rebuild only after a
            # real compatibility migration so update triggers cannot operate
            # on a stale index and report a misleading malformed-database
            # error on the first memory correction.
            self._rebuild_fts()

    @staticmethod
    def _detect_legacy_ltm_unique(conn: sqlite3.Connection) -> bool:
        """Detect the old table-level UNIQUE(category, key) constraint."""

        row = conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'table' AND name = 'long_term_entries'"
        ).fetchone()
        if not row or not row[0]:
            return False
        normalized = "".join(str(row[0]).upper().split())
        return "UNIQUE(CATEGORY,KEY)" in normalized

    def close(self):
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

    def _now(self) -> float:
        return datetime.now(timezone.utc).timestamp()

    # ── Chat Message Buffer ─────────────────────────────────────

    def add_chat_buffer(self, chat_id: str, chat_type: str, sender_name: str,
                        content: str, user_id: int = 0, is_bot: bool = False,
                        message_id: str = "") -> int:
        conn = self._get_conn()
        row = conn.execute(
            """INSERT INTO chat_message_buffer
               (chat_id, chat_type, user_id, sender_name, content, is_bot, created_at, message_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (chat_id, chat_type, user_id, sender_name, content[:2000],
             1 if is_bot else 0, self._now(), message_id),
        )
        conn.commit()
        return row.lastrowid

    def get_chat_buffer(self, chat_id: str, limit: int = 20,
                        before_ts: float = None,
                        *, chat_type: Optional[str] = None) -> list:
        conn = self._get_conn()
        conditions = ["chat_id = ?"]
        params: List[Any] = [chat_id]
        if chat_type:
            conditions.append("chat_type = ?")
            params.append(str(chat_type).strip().lower())
        where = " AND ".join(conditions)
        if before_ts:
            query = (
                "SELECT sender_name, content, is_bot, created_at "
                "FROM chat_message_buffer WHERE "
                + where
                + " AND created_at < ? ORDER BY created_at DESC LIMIT ?"
            )
            rows = conn.execute(query, (*params, before_ts, limit)).fetchall()
        else:
            query = (
                "SELECT sender_name, content, is_bot, created_at "
                "FROM chat_message_buffer WHERE "
                + where
                + " ORDER BY created_at DESC LIMIT ?"
            )
            rows = conn.execute(query, (*params, limit)).fetchall()
        return [
            {"sender": r[0], "text": r[1], "is_bot": bool(r[2]),
             "ts": r[3]}
            for r in reversed(rows)
        ]

    def get_message_by_id(self, message_id: str) -> dict:
        """Look up a single chat message by its NapCat message_id."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT sender_name, content, message_id, created_at FROM chat_message_buffer WHERE message_id = ? LIMIT 1",
            (message_id,),
        ).fetchone()
        if row:
            return {"sender": row[0], "text": row[1], "message_id": row[2], "ts": row[3]}
        return {}

    def trim_chat_buffer(self, chat_id: str, keep: int = 200,
                         *, chat_type: Optional[str] = None) -> int:
        conn = self._get_conn()
        conditions = ["chat_id = ?"]
        params: List[Any] = [chat_id]
        if chat_type:
            conditions.append("chat_type = ?")
            params.append(str(chat_type).strip().lower())
        where = " AND ".join(conditions)
        row = conn.execute(
            "SELECT id FROM chat_message_buffer WHERE " + where
            + " ORDER BY created_at DESC LIMIT 1 OFFSET ?",
            (*params, keep),
        ).fetchone()
        if row:
            conn.execute(
                "DELETE FROM chat_message_buffer WHERE " + where + " AND id <= ?",
                (*params, row[0]),
            )
            conn.commit()
            return conn.total_changes
        return 0

    def get_all_chat_ids(self, chat_type: str = None) -> list:
        """Return distinct chat_ids from the buffer, optionally filtered by type."""
        conn = self._get_conn()
        if chat_type:
            rows = conn.execute(
                "SELECT DISTINCT chat_id FROM chat_message_buffer WHERE chat_type = ?",
                (chat_type,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT DISTINCT chat_id FROM chat_message_buffer",
            ).fetchall()
        return rows

    # ── Short-Term Memory ──────────────────────────────────────

    def add_short_term(self, entry: ShortTermEntry) -> int:
        conn = self._get_conn()
        if entry.created_at <= 0:
            entry.created_at = self._now()
        row = conn.execute(
            """INSERT INTO short_term_entries
               (session_id, turn_index, role, speaker_name, chat_type,
                content, topics, intent, emotional_tone, created_at, bot_replied)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                entry.session_id, entry.turn_index, entry.role,
                entry.speaker_name, entry.chat_type,
                entry.content, json.dumps(entry.topics, ensure_ascii=False),
                entry.intent, entry.emotional_tone, entry.created_at,
                1 if entry.bot_replied else 0,
            ),
        )
        conn.commit()
        return row.lastrowid

    def get_session_entries(self, session_id: str, last_n: int = 20) -> List[ShortTermEntry]:
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM short_term_entries
               WHERE session_id = ? AND summarized = 0
               ORDER BY turn_index DESC LIMIT ?""",
            (session_id, last_n),
        ).fetchall()
        return [_row_to_short_term(r) for r in reversed(rows)]

    def mark_summarized(self, session_id: str, max_turn: int):
        conn = self._get_conn()
        conn.execute(
            "UPDATE short_term_entries SET summarized = 1 WHERE session_id = ? AND turn_index <= ?",
            (session_id, max_turn),
        )
        conn.commit()

    def get_unsummarized_topics(self, session_id: str) -> List[str]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT topics FROM short_term_entries WHERE session_id = ? AND summarized = 0 AND topics != '[]'",
            (session_id,),
        ).fetchall()
        all_topics: List[str] = []
        for r in rows:
            try:
                all_topics.extend(json.loads(r["topics"]))
            except (json.JSONDecodeError, TypeError):
                pass
        return list(dict.fromkeys(all_topics))  # unique, preserve order

    def prune_short_term(self, max_age_days: float = 7.0):
        conn = self._get_conn()
        cutoff = self._now() - (max_age_days * 86400)
        conn.execute("DELETE FROM short_term_entries WHERE created_at < ?", (cutoff,))
        conn.commit()

    # ── Long-Term Memory ───────────────────────────────────────

    def upsert_long_term(self, entry: LongTermEntry) -> int:
        conn = self._get_conn()
        now = self._now()
        if entry.created_at <= 0:
            entry.created_at = now
        entry.updated_at = now

        existing = conn.execute(
            "SELECT id, source_session_ids FROM long_term_entries "
            "WHERE category = ? AND key = ? AND active = 1 ORDER BY updated_at DESC LIMIT 1",
            (entry.category, entry.key),
        ).fetchone()

        if existing:
            try:
                prior_ids = json.loads(existing["source_session_ids"] or "[]")
            except Exception:
                prior_ids = []
            merged = list(dict.fromkeys(prior_ids + entry.source_session_ids))
            conn.execute(
                """UPDATE long_term_entries SET value=?, tags=?, confidence=?,
                   source_session_ids=?, updated_at=? WHERE id=?""",
                (entry.value,
                 json.dumps(entry.tags, ensure_ascii=False),
                 entry.confidence,
                 json.dumps(merged, ensure_ascii=False),
                 entry.updated_at, existing["id"]),
            )
            conn.commit()
            return existing["id"]

        row = conn.execute(
            """INSERT INTO long_term_entries
               (category, key, value, tags, confidence, source_session_ids,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (entry.category, entry.key, entry.value,
             json.dumps(entry.tags, ensure_ascii=False),
             entry.confidence,
             json.dumps(entry.source_session_ids, ensure_ascii=False),
             entry.created_at, entry.updated_at),
        )
        conn.commit()
        return row.lastrowid

    def get_long_term(
        self,
        category: Optional[str] = None,
        limit: int = 50,
        *,
        include_inactive: bool = False,
    ) -> List[LongTermEntry]:
        conn = self._get_conn()
        conditions = []
        params: List[Any] = []
        if category:
            conditions.append("category = ?")
            params.append(category)
        if not include_inactive:
            conditions.append("active = 1")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        params.append(limit)
        rows = conn.execute(
            "SELECT * FROM long_term_entries" + where
            + " ORDER BY confidence DESC, retrieval_count DESC LIMIT ?",
            params,
        ).fetchall()
        return [_row_to_long_term(r) for r in rows]

    def get_long_term_by_id(self, entry_id: int) -> Optional[LongTermEntry]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM long_term_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        return _row_to_long_term(row) if row else None

    def create_memory_edge(self, src_id: int, dst_id: int,
                           relation: str, weight: float = 0.5) -> int:
        conn = self._get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO memory_edges (src_id, dst_id, relation, weight, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (src_id, dst_id, relation, weight, self._now()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM memory_edges WHERE src_id=? AND dst_id=? AND relation=?",
            (src_id, dst_id, relation),
        ).fetchone()
        return row[0] if row else 0

    def supersede_memory(self, old_id: int, new_value: str,
                         new_confidence: float, reason: str = "user_correction") -> int:
        """L2 supersede: mark old active=0, clone with new value, link via corrected_by edge.

        Returns new entry id. Caller responsible for date resolution on new_value.
        """
        conn = self._get_conn()
        old = conn.execute(
            "SELECT * FROM long_term_entries WHERE id = ?", (old_id,)
        ).fetchone()
        if not old:
            return 0

        now = self._now()
        old_key = str(old["key"])
        conn.execute(
            "UPDATE long_term_entries SET active = 0, deleted_at = ? WHERE id = ?",
            (str(now), old_id),
        )

        # v1 databases still enforce uniqueness across inactive rows.  Keep
        # the old row for audit/history but move its key to an internal,
        # collision-free tombstone so the corrected active row can retain the
        # logical key callers use for lookup.  New schemas use the partial
        # active-only index and never enter this branch.
        if getattr(self, "_legacy_ltm_unique", False):
            conn.execute(
                "UPDATE long_term_entries SET key = ? WHERE id = ?",
                (f"{old_key}__superseded_{old_id}", old_id),
            )

        import json as _json
        old_type_data = old["type_data"] if "type_data" in old.keys() else "{}"
        try:
            td = _json.loads(old_type_data) if isinstance(old_type_data, str) else dict(old_type_data)
        except Exception:
            td = {}
        td["previous"] = old["value"]
        td["correction_reason"] = reason
        if getattr(self, "_legacy_ltm_unique", False):
            td["original_key"] = old_key

        cursor = conn.execute(
            """INSERT INTO long_term_entries
               (category, key, value, tags, confidence, source_session_ids,
                retrieval_count, last_retrieved, created_at, updated_at,
                memory_type, type_data, salience, recall_strength,
                reconsolidation_count, last_recalled_at,
                source_user_id, source_message_ts, source_context,
                derivation, supersedes_id, active)
               VALUES (?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, 1.0, 0, NULL, ?, ?, ?, 'corrected', ?, 1)""",
            (old["category"], old_key, new_value, old["tags"],
             new_confidence, old["source_session_ids"],
             now, now,
             old["memory_type"] if "memory_type" in old.keys() else "semantic",
             _json.dumps(td, ensure_ascii=False),
             max(old["salience"] if "salience" in old.keys() else 0.5, 0.8),
             old["source_user_id"] if "source_user_id" in old.keys() else "",
             old["source_message_ts"] if "source_message_ts" in old.keys() else "",
             old["source_context"] if "source_context" in old.keys() else "",
             old_id),
        )
        new_id = cursor.lastrowid
        conn.execute(
            "INSERT OR IGNORE INTO memory_edges (src_id, dst_id, relation, weight, created_at) "
            "VALUES (?, ?, 'corrected_by', 1.0, ?)",
            (new_id, old_id, now),
        )
        conn.commit()
        return new_id

    def mark_memory_doubt(self, entry_id: int):
        """L5 metamemory: mark a memory as uncertain (needs_review)."""
        conn = self._get_conn()
        import json as _json
        row = conn.execute(
            "SELECT type_data FROM long_term_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if row:
            try:
                td = _json.loads(row[0]) if row[0] else {}
            except Exception:
                td = {}
            td["metamemory_doubt"] = True
            conn.execute(
                "UPDATE long_term_entries SET type_data = ? WHERE id = ?",
                (_json.dumps(td, ensure_ascii=False), entry_id),
            )
            conn.commit()

    def search_by_user_id(self, user_id: str, limit: int = 10) -> List[LongTermEntry]:
        """Exact-match lookup on source_user_id column (not covered by FTS)."""
        if not user_id:
            return []
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM long_term_entries WHERE source_user_id = ? AND active = 1 "
            "ORDER BY confidence DESC, created_at DESC LIMIT ?",
            (str(user_id), limit),
        ).fetchall()
        return [_row_to_long_term(r) for r in rows]

    def search_long_term(self, query: str, limit: int = 10) -> List[LongTermEntry]:
        conn = self._get_conn()
        seen_ids = set()
        scored: list = []

        import re as _re
        terms = [t for t in _re.split(r'[\s,，。、]+', query.strip()) if t]
        if not terms:
            terms = [query]

        # Single FTS query with phrase-quoted terms (prevents FTS5 operator injection:
        # bare AND/OR/NOT/NEAR would be interpreted as boolean operators).
        fts_query = " ".join(f'"{t.replace(chr(34), "")}"' for t in terms[:6])
        try:
            fts_rows = conn.execute(
                """SELECT le.* FROM long_term_entries le
                   JOIN lte_fts fts ON le.id = fts.rowid
                   WHERE le.active = 1 AND lte_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (fts_query, limit * 2),
            ).fetchall()
            for r in fts_rows:
                rid = r["id"] if hasattr(r, "keys") else r[0]
                if rid not in seen_ids:
                    seen_ids.add(rid)
                    scored.append((r, 1.0))
        except sqlite3.OperationalError:
            pass

        # Single LIKE query OR-ing all terms (CJK substring match — the reliable path).
        like_clauses = []
        like_params = []
        for t in terms[:6]:
            like_clauses.append("key LIKE ? OR value LIKE ? OR tags LIKE ?")
            like_params.extend([f"%{t}%", f"%{t}%", f"%{t}%"])
        if like_clauses:
            try:
                like_sql = (
                    "SELECT * FROM long_term_entries WHERE active = 1 AND ("
                    + " OR ".join(like_clauses)
                    + ") LIMIT ?"
                )
                like_rows = conn.execute(like_sql, like_params + [limit * 3]).fetchall()
                for r in like_rows:
                    rid = r["id"] if hasattr(r, "keys") else r[0]
                    if rid not in seen_ids:
                        seen_ids.add(rid)
                        scored.append((r, 0.8))
            except Exception:
                pass

        def _sort_key(item):
            r, src_bonus = item
            d = dict(r) if hasattr(r, "keys") else {}
            conf = d.get("confidence", 0.5) or 0.5
            rc = d.get("retrieval_count", 0) or 0
            return -(src_bonus + conf + min(rc / 50.0, 0.3))

        scored.sort(key=_sort_key)
        return [_row_to_long_term(r) for r, _ in scored[:limit]]

    # ── 核心记忆（soul 自传体） ─────────────────────────────

    def add_core_memory(self, category: str, content: str,
                        source: str = "self_write",
                        occurred_at: Optional[float] = None) -> int:
        import time as _t
        now = _t.time()
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO core_memories (category, content, source, occurred_at, "
            "created_at, updated_at, active) VALUES (?, ?, ?, ?, ?, ?, 1)",
            (category, content, source, occurred_at or now, now, now),
        )
        conn.commit()
        return cur.lastrowid

    def soft_delete_core_memory(self, mem_id: int) -> bool:
        import time as _t
        conn = self._get_conn()
        cur = conn.execute(
            "UPDATE core_memories SET active = 0, deleted_at = ?, updated_at = ? "
            "WHERE id = ? AND active = 1",
            (_t.time(), _t.time(), mem_id),
        )
        conn.commit()
        return cur.rowcount > 0

    def list_core_memories(self, category: Optional[str] = None,
                           include_inactive: bool = False) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        sql = "SELECT id, category, content, source, occurred_at, created_at, active " \
              "FROM core_memories"
        conditions = []
        params: List[Any] = []
        if not include_inactive:
            conditions.append("active = 1")
        if category:
            conditions.append("category = ?")
            params.append(category)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY COALESCE(occurred_at, created_at) ASC"
        rows = conn.execute(sql, params).fetchall()
        return [
            {"id": r[0], "category": r[1], "content": r[2], "source": r[3],
             "occurred_at": r[4], "created_at": r[5], "active": r[6]}
            for r in rows
        ]

    def load_core_memories_prompt(self) -> str:
        rows = self.list_core_memories()
        if not rows:
            return ""
        by_cat: Dict[str, List[str]] = {}
        for r in rows:
            cat = r["category"] or "general"
            by_cat.setdefault(cat, []).append(f"- {r['content']}")
        parts = ["[核心记忆]"]
        for cat in by_cat:
            parts.append(f"## {cat}")
            parts.extend(by_cat[cat])
        return "\n".join(parts)

    def record_ltm_retrieval(self, entry_id: int):
        conn = self._get_conn()
        conn.execute(
            "UPDATE long_term_entries SET retrieval_count = retrieval_count + 1, last_retrieved = ? WHERE id = ?",
            (self._now(), entry_id),
        )
        conn.commit()

    def delete_long_term(self, entry_id: int):
        conn = self._get_conn()
        conn.execute("DELETE FROM long_term_entries WHERE id = ?", (entry_id,))
        conn.commit()

    def get_ltm_by_confidence(self, min_confidence: float = 0.3) -> List[LongTermEntry]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM long_term_entries WHERE active = 1 AND confidence >= ? "
            "ORDER BY confidence DESC",
            (min_confidence,),
        ).fetchall()
        return [_row_to_long_term(r) for r in rows]

    # ── Workflow Memory ────────────────────────────────────────

    def upsert_workflow(self, entry: WorkflowEntry) -> int:
        conn = self._get_conn()
        now = self._now()
        if entry.created_at <= 0:
            entry.created_at = now
        entry.updated_at = now

        try:
            row = conn.execute(
                """INSERT INTO workflow_entries
                   (name, description, trigger_patterns, steps, preconditions,
                    expected_outcome, usage_count, success_count, base_weight,
                    current_weight, last_used, created_at, updated_at,
                    source_session_ids, version, skill_md_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                   description=excluded.description,
                   trigger_patterns=excluded.trigger_patterns,
                   steps=excluded.steps,
                   preconditions=excluded.preconditions,
                   expected_outcome=excluded.expected_outcome,
                   base_weight=excluded.base_weight,
                   current_weight=excluded.current_weight,
                   source_session_ids=(
                       SELECT json_group_array(val) FROM (
                           SELECT value AS val FROM json_each(
                               COALESCE(workflow_entries.source_session_ids, '[]')
                           )
                           UNION
                           SELECT value AS val FROM json_each(
                               COALESCE(excluded.source_session_ids, '[]')
                           )
                       )
                   ),
                   version=excluded.version,
                   skill_md_path=excluded.skill_md_path,
                   updated_at=excluded.updated_at""",
                (
                    entry.name, entry.description,
                    json.dumps(entry.trigger_patterns, ensure_ascii=False),
                    json.dumps(entry.steps, ensure_ascii=False),
                    json.dumps(entry.preconditions, ensure_ascii=False),
                    entry.expected_outcome,
                    entry.usage_count, entry.success_count,
                    entry.base_weight, entry.current_weight,
                    entry.last_used, entry.created_at, entry.updated_at,
                    json.dumps(entry.source_session_ids, ensure_ascii=False),
                    entry.version, entry.skill_md_path,
                ),
            )
            conn.commit()
            return row.lastrowid
        except sqlite3.IntegrityError:
            existing = conn.execute(
                "SELECT id FROM workflow_entries WHERE name = ?", (entry.name,)
            ).fetchone()
            if existing:
                prior_ids = json.loads(
                    conn.execute(
                        "SELECT source_session_ids FROM workflow_entries WHERE id = ?",
                        (existing["id"],),
                    ).fetchone()["source_session_ids"] or "[]"
                )
                merged = list(dict.fromkeys(prior_ids + entry.source_session_ids))
                conn.execute(
                    """UPDATE workflow_entries SET description=?, trigger_patterns=?,
                       steps=?, preconditions=?, expected_outcome=?, base_weight=?,
                       current_weight=?, source_session_ids=?, version=?,
                       skill_md_path=?, updated_at=? WHERE id=?""",
                    (
                        entry.description,
                        json.dumps(entry.trigger_patterns, ensure_ascii=False),
                        json.dumps(entry.steps, ensure_ascii=False),
                        json.dumps(entry.preconditions, ensure_ascii=False),
                        entry.expected_outcome,
                        entry.base_weight, entry.current_weight,
                        json.dumps(merged, ensure_ascii=False),
                        entry.version, entry.skill_md_path, entry.updated_at,
                        existing["id"],
                    ),
                )
                conn.commit()
                return existing["id"]
            raise

    def record_workflow_usage(self, workflow_id: int, success: bool = True):
        conn = self._get_conn()
        now = self._now()
        conn.execute(
            """UPDATE workflow_entries
               SET usage_count = usage_count + 1,
                   success_count = success_count + ?,
                   last_used = ?,
                   updated_at = ?
               WHERE id = ?""",
            (1 if success else 0, now, now, workflow_id),
        )
        conn.commit()

    def get_workflow(self, name: str) -> Optional[WorkflowEntry]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM workflow_entries WHERE name = ?", (name,)
        ).fetchone()
        return _row_to_workflow(row) if row else None

    def get_all_workflows(self) -> List[WorkflowEntry]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM workflow_entries ORDER BY current_weight DESC"
        ).fetchall()
        return [_row_to_workflow(r) for r in rows]

    def get_active_workflows(self, threshold: float = 0.1) -> List[WorkflowEntry]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM workflow_entries WHERE current_weight >= ? ORDER BY current_weight DESC",
            (threshold,),
        ).fetchall()
        return [_row_to_workflow(r) for r in rows]

    def search_workflows(self, query: str, limit: int = 10) -> List[WorkflowEntry]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT we.* FROM workflow_entries we
                   JOIN wfe_fts fts ON we.id = fts.rowid
                   WHERE wfe_fts MATCH ?
                   ORDER BY we.current_weight * rank DESC
                   LIMIT ?""",
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute(
                "SELECT * FROM workflow_entries WHERE name LIKE ? OR description LIKE ? ORDER BY current_weight DESC LIMIT ?",
                (f"%{query}%", f"%{query}%", limit),
            ).fetchall()
        return [_row_to_workflow(r) for r in rows]

    def delete_workflow(self, workflow_id: int):
        conn = self._get_conn()
        conn.execute("DELETE FROM workflow_entries WHERE id = ?", (workflow_id,))
        conn.commit()

    def get_stale_workflows(self, stale_days: float = 30.0) -> List[WorkflowEntry]:
        """Get workflows not used within stale_days (for decay/culling)."""
        conn = self._get_conn()
        cutoff = self._now() - (stale_days * 86400)
        rows = conn.execute(
            "SELECT * FROM workflow_entries WHERE last_used > 0 AND last_used < ? ORDER BY current_weight ASC",
            (cutoff,),
        ).fetchall()
        return [_row_to_workflow(r) for r in rows]

    # ── Wiki Knowledge ─────────────────────────────────────────

    def upsert_wiki(self, entry: WikiEntry) -> int:
        conn = self._get_conn()
        now = self._now()
        if entry.created_at <= 0:
            entry.created_at = now
        entry.updated_at = now

        try:
            row = conn.execute(
                """INSERT INTO wiki_entries
                   (source_url, title, section, content, chunk_index, tags,
                    embedding_hash, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source_url, chunk_index) DO UPDATE SET
                   title=excluded.title,
                   section=excluded.section,
                   content=excluded.content,
                   tags=excluded.tags,
                   embedding_hash=excluded.embedding_hash,
                   updated_at=excluded.updated_at""",
                (
                    entry.source_url, entry.title, entry.section,
                    entry.content, entry.chunk_index,
                    json.dumps(entry.tags, ensure_ascii=False),
                    entry.embedding_hash, entry.created_at, entry.updated_at,
                ),
            )
            conn.commit()
            return row.lastrowid
        except sqlite3.IntegrityError:
            existing = conn.execute(
                "SELECT id FROM wiki_entries WHERE source_url = ? AND chunk_index = ?",
                (entry.source_url, entry.chunk_index),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE wiki_entries SET title=?, section=?, content=?,
                       tags=?, embedding_hash=?, updated_at=? WHERE id=?""",
                    (
                        entry.title, entry.section, entry.content,
                        json.dumps(entry.tags, ensure_ascii=False),
                        entry.embedding_hash, entry.updated_at,
                        existing["id"],
                    ),
                )
                conn.commit()
                return existing["id"]
            raise

    def search_wiki(self, query: str, limit: int = 10) -> List[WikiEntry]:
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """SELECT we.* FROM wiki_entries we
                   JOIN we_fts fts ON we.id = fts.rowid
                   WHERE we_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute(
                "SELECT * FROM wiki_entries WHERE title LIKE ? OR content LIKE ? LIMIT ?",
                (f"%{query}%", f"%{query}%", limit),
            ).fetchall()
        return [_row_to_wiki(r) for r in rows]

    def get_wiki_by_title(self, title: str) -> List[WikiEntry]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM wiki_entries WHERE title = ? ORDER BY chunk_index",
            (title,),
        ).fetchall()
        return [_row_to_wiki(r) for r in rows]

    def record_wiki_retrieval(self, entry_id: int):
        conn = self._get_conn()
        conn.execute(
            "UPDATE wiki_entries SET retrieval_count = retrieval_count + 1, last_retrieved = ? WHERE id = ?",
            (self._now(), entry_id),
        )
        conn.commit()

    def get_wiki_stats(self) -> Dict[str, Any]:
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM wiki_entries").fetchone()[0]
        titles = conn.execute("SELECT COUNT(DISTINCT title) FROM wiki_entries").fetchone()[0]
        return {"total_chunks": total, "unique_titles": titles, "last_sync": None}

    def clear_wiki(self):
        conn = self._get_conn()
        conn.execute("DELETE FROM wiki_entries")
        conn.commit()

    # ── Consolidation Log ──────────────────────────────────────

    def log_consolidation(self, session_id: str, source_type: str, target_type: str,
                          source_id: int, target_id: int, action: str, detail: str = ""):
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO consolidation_log
               (session_id, source_type, target_type, source_id, target_id, action, detail, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, source_type, target_type, source_id, target_id, action, detail, self._now()),
        )
        conn.commit()

    # ── Maintenance ────────────────────────────────────────────

    def _rebuild_fts(self):
        """Rebuild all FTS5 external content indexes after writes."""
        conn = self._get_conn()
        for fts_table in ("lte_fts", "wfe_fts", "we_fts"):
            try:
                conn.execute(f"INSERT INTO {fts_table}({fts_table}) VALUES('rebuild')")
            except sqlite3.DatabaseError:
                try:
                    conn.rollback()
                except Exception:
                    pass
        conn.commit()

    def vacuum(self):
        """Full vacuum — only when free pages exceed threshold."""
        conn = self._get_conn()
        free_pages = conn.execute("PRAGMA freelist_count").fetchone()[0]
        if free_pages > 1000:
            logger = __import__("logging").getLogger(__name__)
            logger.info("Running VACUUM (free_pages=%d)", free_pages)
            conn.execute("PRAGMA optimize")
            conn.execute("VACUUM")

    def quick_maintenance(self):
        """Lightweight maintenance: rebuild FTS5 indexes, pragma optimize."""
        self._rebuild_fts()
        conn = self._get_conn()
        conn.execute("PRAGMA optimize")

    def get_store_stats(self) -> Dict[str, Any]:
        conn = self._get_conn()
        return {
            "short_term_count": conn.execute("SELECT COUNT(*) FROM short_term_entries WHERE summarized = 0").fetchone()[0],
            "long_term_count": conn.execute("SELECT COUNT(*) FROM long_term_entries").fetchone()[0],
            "workflow_count": conn.execute("SELECT COUNT(*) FROM workflow_entries").fetchone()[0],
            "wiki_chunk_count": conn.execute("SELECT COUNT(*) FROM wiki_entries").fetchone()[0],
        }


# ── Row deserialization helpers ────────────────────────────────

def _parse_json(raw: Any, default: Any = None) -> Any:
    if default is None:
        default = []
    if raw is None:
        return default
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _row_to_short_term(row: sqlite3.Row) -> ShortTermEntry:
    return ShortTermEntry(
        id=row["id"],
        session_id=row["session_id"],
        turn_index=row["turn_index"],
        role=row["role"],
        speaker_name=row["speaker_name"] or "",
        chat_type=row["chat_type"] or "dm",
        content=row["content"],
        topics=_parse_json(row["topics"]),
        intent=row["intent"] or "",
        emotional_tone=row["emotional_tone"] or "",
        created_at=row["created_at"] or 0.0,
        summarized=bool(row["summarized"]),
        bot_replied=bool(row["bot_replied"]) if "bot_replied" in row.keys() else True,
    )


def _row_to_long_term(row: sqlite3.Row) -> LongTermEntry:
    keys = set(row.keys())
    return LongTermEntry(
        id=row["id"],
        category=row["category"],
        key=row["key"],
        value=row["value"],
        tags=_parse_json(row["tags"]),
        confidence=row["confidence"],
        source_session_ids=_parse_json(row["source_session_ids"]),
        retrieval_count=row["retrieval_count"] or 0,
        last_retrieved=row["last_retrieved"] or 0.0,
        ttl_days=row["ttl_days"] if "ttl_days" in keys else None,
        created_at=row["created_at"] or 0.0,
        updated_at=row["updated_at"] or 0.0,
        memory_type=(row["memory_type"] or "semantic") if "memory_type" in keys else "semantic",
        type_data=_parse_json(row["type_data"], {}) if "type_data" in keys else {},
        salience=(row["salience"] if row["salience"] is not None else 0.5) if "salience" in keys else 0.5,
        recall_strength=(row["recall_strength"] if row["recall_strength"] is not None else 1.0) if "recall_strength" in keys else 1.0,
        reconsolidation_count=(row["reconsolidation_count"] or 0) if "reconsolidation_count" in keys else 0,
        last_recalled_at=row["last_recalled_at"] if "last_recalled_at" in keys else None,
        source_user_id=(row["source_user_id"] or "") if "source_user_id" in keys else "",
        source_message_ts=(row["source_message_ts"] or "") if "source_message_ts" in keys else "",
        source_context=(row["source_context"] or "") if "source_context" in keys else "",
        derivation=(row["derivation"] or "direct") if "derivation" in keys else "direct",
        supersedes_id=row["supersedes_id"] if "supersedes_id" in keys else None,
        active=bool(row["active"]) if "active" in keys else True,
        deleted_at=row["deleted_at"] if "deleted_at" in keys else None,
    )


def _row_to_workflow(row: sqlite3.Row) -> WorkflowEntry:
    return WorkflowEntry(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        trigger_patterns=_parse_json(row["trigger_patterns"]),
        steps=_parse_json(row["steps"]),
        preconditions=_parse_json(row["preconditions"]),
        expected_outcome=row["expected_outcome"] or "",
        usage_count=row["usage_count"] or 0,
        success_count=row["success_count"] or 0,
        base_weight=row["base_weight"],
        current_weight=row["current_weight"],
        last_used=row["last_used"] or 0.0,
        created_at=row["created_at"] or 0.0,
        updated_at=row["updated_at"] or 0.0,
        source_session_ids=_parse_json(row["source_session_ids"]),
        version=row["version"] or 1,
        skill_md_path=row["skill_md_path"] or "",
    )


def _row_to_wiki(row: sqlite3.Row) -> WikiEntry:
    return WikiEntry(
        id=row["id"],
        source_url=row["source_url"],
        title=row["title"],
        section=row["section"] or "",
        content=row["content"],
        chunk_index=row["chunk_index"] or 0,
        tags=_parse_json(row["tags"]),
        embedding_hash=row["embedding_hash"] or "",
        retrieval_count=row["retrieval_count"] or 0,
        last_retrieved=row["last_retrieved"] or 0.0,
        created_at=row["created_at"] or 0.0,
        updated_at=row["updated_at"] or 0.0,
    )
