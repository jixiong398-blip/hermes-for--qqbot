"""
Hermes Memory v2 Schema Migration — non-destructive ALTER TABLE.

Adds closed-loop columns to long_term_entries, creates new tables.
Existing data preserved; new columns get defaults.
"""
import sqlite3
import json
import math
import time
import sys
from pathlib import Path

DB_PATH = Path.home() / ".hermes" / "memory_store.db"


def migrate():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.cursor()

    existing = {row[1] for row in cur.execute("SELECT * FROM pragma_table_info('long_term_entries')").fetchall()}
    print(f"Existing columns: {len(existing)}")

    # ── 1. Add new columns (non-destructive ALTER TABLE) ──
    new_cols = [
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
    ]
    for col_name, col_def in new_cols:
        if col_name not in existing:
            print(f"  Adding column: {col_name}")
            cur.execute(f"ALTER TABLE long_term_entries ADD COLUMN {col_name} {col_def}")
        else:
            print(f"  Skip existing: {col_name}")

    # ── 2. Create memory_edges table ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS memory_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            src_id INTEGER NOT NULL,
            dst_id INTEGER NOT NULL,
            relation TEXT NOT NULL,
            weight REAL DEFAULT 0.5,
            created_at TEXT NOT NULL,
            UNIQUE(src_id, dst_id, relation),
            FOREIGN KEY (src_id) REFERENCES long_term_entries(id),
            FOREIGN KEY (dst_id) REFERENCES long_term_entries(id)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_src ON memory_edges(src_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_edges_dst ON memory_edges(dst_id)")

    # ── 3. Create _sleep_watermark ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS _sleep_watermark (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    # ── 4. Create registry tables (v2 seed) ──
    cur.execute("""
        CREATE TABLE IF NOT EXISTS _memory_type_registry (
            type_name TEXT PRIMARY KEY,
            type_data_schema TEXT,
            description TEXT,
            created_at TEXT,
            created_by TEXT DEFAULT 'system'
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS _relation_registry (
            relation TEXT PRIMARY KEY,
            description TEXT,
            is_traversable INTEGER DEFAULT 1,
            created_by TEXT DEFAULT 'system'
        )
    """)

    # Seed initial registry entries
    import datetime
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for tname, tdesc in [
        ("semantic", "Persistent facts/knowledge about users, world, self"),
        ("episodic", "Conversation segments (wake→silence), source for abstraction"),
        ("procedural", "Learned behavior patterns: triggers + action templates"),
    ]:
        cur.execute(
            "INSERT OR IGNORE INTO _memory_type_registry (type_name, description, created_at) VALUES (?, ?, ?)",
            (tname, tdesc, now),
        )
    for rel, desc, trav in [
        ("related_to", "General association", 1),
        ("supports", "One memory supports/confirms another", 1),
        ("contradicts", "Conflicting memories", 1),
        ("abstracts_from", "Semantic fact abstracted from episodic cluster", 0),
        ("corrected_by", "New memory supersedes old via correction", 0),
    ]:
        cur.execute(
            "INSERT OR IGNORE INTO _relation_registry (relation, description, is_traversable, created_by) VALUES (?, ?, ?, 'system')",
            (rel, desc, trav),
        )

    # ── 5. Migrate existing rows: populate new columns ──
    rows = cur.execute("SELECT id, category, key, value, confidence, ttl_days, created_at, updated_at, source_session_ids, tags FROM long_term_entries").fetchall()
    print(f"\nMigrating {len(rows)} existing rows...")

    for row in rows:
        eid, cat, key, value, conf, ttl, created_at, updated_at, session_ids, tags = row
        # Map old category→subcategory in type_data
        type_data = json.dumps({
            "subcategory": cat,
            "key": key,
        })
        # Compute initial recall_strength from age
        age_days = (time.time() - created_at) / 86400.0 if created_at else 365.0
        # For old entries, start with decayed strength
        # S=conf (salience proxy), R=1.0 (no reconsolidation yet), λ=0.02
        S = max(0.3, conf)  # min salience 0.3
        recall_strength = S * 1.0 * math.exp(-0.02 * age_days / S)
        recall_strength = max(0.05, min(1.0, recall_strength))

        cur.execute("""
            UPDATE long_term_entries SET
                memory_type = 'semantic',
                type_data = ?,
                salience = ?,
                recall_strength = ?,
                derivation = 'legacy',
                active = 1
            WHERE id = ?
        """, (type_data, conf, round(recall_strength, 4), eid))

    # ── 6. Create new indexes ──
    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_ltm_type ON long_term_entries(memory_type) WHERE active=1",
        "CREATE INDEX IF NOT EXISTS idx_ltm_source_user ON long_term_entries(source_user_id) WHERE active=1",
        "CREATE INDEX IF NOT EXISTS idx_ltm_salience ON long_term_entries(salience)",
        "CREATE INDEX IF NOT EXISTS idx_ltm_recall ON long_term_entries(last_recalled_at)",
        "CREATE INDEX IF NOT EXISTS idx_ltm_active ON long_term_entries(active)",
    ]:
        try:
            cur.execute(idx_sql)
        except Exception as e:
            print(f"  Index warning: {e}")

    conn.commit()
    conn.close()
    print("\nMigration complete.")


def verify():
    """Verify schema after migration."""
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()

    # Check new columns
    cols = {row[1]: row[2] for row in cur.execute("SELECT * FROM pragma_table_info('long_term_entries')").fetchall()}
    required = ["memory_type", "type_data", "salience", "recall_strength", "derivation", "active", "supersedes_id"]
    for c in required:
        status = "✓" if c in cols else "✗ MISSING"
        print(f"  {status} {c} ({cols.get(c, 'N/A')})")

    # Check new tables
    for tbl in ["memory_edges", "_sleep_watermark", "_memory_type_registry", "_relation_registry"]:
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,))
        status = "✓" if cur.fetchone() else "✗ MISSING"
        print(f"  {status} table {tbl}")

    # Check migration
    cur.execute("SELECT COUNT(*), SUM(CASE WHEN derivation='legacy' THEN 1 ELSE 0 END) FROM long_term_entries")
    total, legacy = cur.fetchone()
    print(f"\n  Total entries: {total}")
    print(f"  Migrated (legacy): {legacy}")
    cur.execute("SELECT memory_type, COUNT(*) FROM long_term_entries GROUP BY memory_type")
    for mt, cnt in cur.fetchall():
        print(f"    {mt}: {cnt}")

    # Sample a migrated row
    cur.execute("SELECT id, memory_type, type_data, salience, recall_strength, derivation, active FROM long_term_entries WHERE derivation='legacy' LIMIT 3")
    for r in cur.fetchall():
        print(f"\n  Sample: id={r[0]} type={r[1]} salience={r[3]:.2f} recall={r[4]:.3f} deriv={r[5]} active={r[6]}")
        print(f"    type_data: {r[2][:100]}")

    conn.close()


if __name__ == "__main__":
    if "--verify" in sys.argv:
        verify()
    else:
        migrate()
        verify()
