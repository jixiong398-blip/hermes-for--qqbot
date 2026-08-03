"""Drop UNIQUE(category,key) from long_term_entries.

Replaces table-level constraint with application-level dedup.
Supersede no longer needs key-suffix hack.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path.home() / ".hermes" / "memory_store.db"


def migrate():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")

    # Recreate long_term_entries without UNIQUE constraint
    conn.executescript("""
        CREATE TABLE long_term_entries_new (
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
            created_at REAL DEFAULT (strftime('%s','now')),
            updated_at REAL DEFAULT (strftime('%s','now')),
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

        INSERT INTO long_term_entries_new
            (id, category, key, value, tags, confidence, source_session_ids,
             retrieval_count, last_retrieved, created_at, updated_at, ttl_days,
             memory_type, type_data, salience, recall_strength,
             reconsolidation_count, last_recalled_at, source_user_id,
             source_message_ts, source_context, derivation, supersedes_id,
             active, deleted_at)
        SELECT id, category, key, value, tags, confidence, source_session_ids,
               retrieval_count, last_retrieved, created_at, updated_at, ttl_days,
               memory_type, type_data, salience, recall_strength,
               reconsolidation_count, last_recalled_at, source_user_id,
               source_message_ts, source_context, derivation, supersedes_id,
               active, deleted_at
        FROM long_term_entries;

        DROP TABLE long_term_entries;
        ALTER TABLE long_term_entries_new RENAME TO long_term_entries;

        -- Partial unique: only active rows must be unique
        CREATE UNIQUE INDEX IF NOT EXISTS idx_ltm_unique_active
            ON long_term_entries(category, key) WHERE active=1;
    """)

    # Recreate other indexes
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_ltm_type ON long_term_entries(memory_type) WHERE active=1",
        "CREATE INDEX IF NOT EXISTS idx_ltm_source_user ON long_term_entries(source_user_id) WHERE active=1",
        "CREATE INDEX IF NOT EXISTS idx_ltm_salience ON long_term_entries(salience)",
        "CREATE INDEX IF NOT EXISTS idx_ltm_recall ON long_term_entries(last_recalled_at)",
    ]:
        conn.execute(idx_sql)

    # Clean up orphaned corrected_by edges (dst entries may have been deleted)
    conn.execute("""
        DELETE FROM memory_edges WHERE dst_id NOT IN (SELECT id FROM long_term_entries)
           OR src_id NOT IN (SELECT id FROM long_term_entries)
    """)

    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()

    # Verify
    cur = conn.execute("SELECT sql FROM sqlite_master WHERE name='long_term_entries'")
    print("New schema:")
    print(cur.fetchone()[0][:200])

    # Check UNIQUE is gone
    idxs = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='long_term_entries'").fetchall()
    print("\nIndexes:")
    for n, s in idxs:
        print(f"  {n}: {s[:80] if s else '(auto)'}")

    # Count
    n = conn.execute("SELECT COUNT(*) FROM long_term_entries").fetchone()[0]
    active = conn.execute("SELECT COUNT(*) FROM long_term_entries WHERE active=1").fetchone()[0]
    print(f"\nRows: {n} total, {active} active")

    conn.close()
    print("Done.")


if __name__ == "__main__":
    migrate()
