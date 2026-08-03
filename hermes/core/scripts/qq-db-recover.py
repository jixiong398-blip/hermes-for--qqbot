#!/usr/bin/env python3
"""Recover missing group messages from a decrypted QQ NT database.

Reads a decrypted nt_msg.db and imports group messages into Hermes's
corpus_messages with message_id dedup. Deletes the decrypted file when done.

Usage:
    python3 scripts/qq-db-recover.py [/path/to/nt_msg_decrypted.db]
"""

import sqlite3, json, os, sys
from datetime import datetime
from pathlib import Path

STATE_DB = Path.home() / ".hermes" / "state.db"
DECRYPTED_DB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.expanduser("~"), ".cache", "nt_msg_decrypted.db"
)


def main():
    if not os.path.exists(DECRYPTED_DB):
        print(f"Error: {DECRYPTED_DB} not found. Run the dbexport plugin first.")
        sys.exit(1)

    src = sqlite3.connect(DECRYPTED_DB)
    dst = sqlite3.connect(str(STATE_DB), timeout=30)
    dst.execute("PRAGMA busy_timeout=60000")
    dst.execute("PRAGMA journal_mode=WAL")

    # Ensure target table exists
    dst.executescript("""
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
        CREATE INDEX IF NOT EXISTS idx_corpus_chat_time ON corpus_messages(chat_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_corpus_group ON corpus_messages(group_id, created_at);
    """)
    dst.commit()

    # Load existing message_ids for dedup
    existing = set()
    for r in dst.execute("SELECT message_id FROM corpus_messages WHERE created_at > ?",
                         (1781942400,)):  # since 2026-06-21
        existing.add(r[0])
    print(f"Existing corpus message_ids in range: {len(existing)}")

    # Get group list from QQ DB
    groups = src.execute(
        "SELECT DISTINCT [40021] FROM group_msg_table WHERE [40021] IS NOT NULL"
    ).fetchall()

    total_inserted = 0
    for (gid,) in groups:
        gname = ""
        print(f"\n--- Group {gid} ---")

        inserted = 0
        for row in src.execute(
            "SELECT [40001], [40002], [40021], [40050], [40090], [40010] "
            "FROM group_msg_table "
            "WHERE [40021] = ? AND [40090] IS NOT NULL AND length([40090]) > 0 "
            "ORDER BY [40050] ASC",
            (gid,),
        ):
            msg_id = str(row[0])
            msg_time = row[3]
            if msg_time <= 0:
                continue

            if msg_id in existing:
                continue

            sender_uin = row[1] or 0
            text = row[4] or ""
            raw = text
            readable = text  # QQ DB text is already plain

            try:
                dst.execute(
                    """INSERT INTO corpus_messages
                       (message_id, chat_id, chat_type, group_id, user_id,
                        sender_name, sender_card, content_raw, content_readable,
                        image_descriptions, at_targets, reply_to_id, reply_to_text,
                        is_bot, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (msg_id, str(gid), "group", str(gid),
                     sender_uin, f"QQ{sender_uin}", "",
                     raw, readable,
                     "[]", "[]", "", "",
                     0, float(msg_time)),
                )
                inserted += 1
                existing.add(msg_id)
            except Exception as e:
                if "UNIQUE" not in str(e):
                    print(f"  Insert error msg_id={msg_id}: {e}")

        dst.commit()
        total_inserted += inserted

        if inserted > 0:
            t0 = datetime.fromtimestamp(
                src.execute("SELECT MIN([40050]) FROM group_msg_table WHERE [40021]=? AND [40050]>0",
                            (gid,)).fetchone()[0]
            ).strftime("%m-%d %H:%M")
            t1 = datetime.fromtimestamp(
                src.execute("SELECT MAX([40050]) FROM group_msg_table WHERE [40021]=?",
                            (gid,)).fetchone()[0]
            ).strftime("%m-%d %H:%M")
            print(f"  +{inserted} messages ({t0} ~ {t1})")
        else:
            print(f"  No new messages")

    src.close()
    dst.close()

    print(f"\nTotal inserted: {total_inserted}")

    # Clean up decrypted DB
    if total_inserted > 0:
        os.remove(DECRYPTED_DB)
        print(f"Cleaned up: {DECRYPTED_DB} removed")
    else:
        print(f"WARNING: nothing inserted, keeping {DECRYPTED_DB} for inspection")


if __name__ == "__main__":
    main()
