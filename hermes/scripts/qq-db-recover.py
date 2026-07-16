#!/usr/bin/env python3
"""Recover group messages from a decrypted QQ NT database into Hermes corpus_messages.

Corrects v0.10.2 bug: [40090] is group nicknames, NOT message text.
Real text is in [40800] protobuf BLOBs, extracted via CJK fragment filtering.

Usage:
    python3 scripts/qq-db-recover.py [/path/to/nt_msg_decrypted.db]
"""

import sqlite3, json, os, sys, re
from datetime import datetime
from pathlib import Path

STATE_DB = Path.home() / ".hermes" / "state.db"
DECRYPTED_DB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.expanduser("~"), ".cache", "nt_msg_decrypted.db"
)

# ── Text extraction from protobuf BLOBs ──────────────────────────────

def extract_text(blob: bytes | None, fallback_40090: str = "") -> str:
    """Extract real message text from [40800] protobuf BLOB.
    
    The BLOB is raw protobuf binary with embedded CJK text fragments.
    We decode UTF-8, strip binary noise (file refs, download URLs, hashes),
    and extract CJK sequences. Falls back to [40090] only if BLOB is empty.
    """
    if not blob:
        # Fallback: [40090] sometimes contains real text for longer messages
        if fallback_40090 and len(fallback_40090.strip()) >= 4:
            return fallback_40090.strip()
        return ""
    
    try:
        decoded = blob.decode("utf-8", errors="ignore")
    except Exception:
        return fallback_40090.strip() if fallback_40090 else ""
    
    # Strip protobuf binary noise
    decoded = re.sub(r'[A-F0-9]{16,}\.(jpg|png|gif|jpeg|bmp|webp)', '', decoded, flags=re.I)
    decoded = re.sub(r'/download\?appid=\d+&fileid=[A-Za-z0-9_\-]+&spec=\d+', '', decoded)
    decoded = re.sub(r'[A-Fa-f0-9]{32,}', '', decoded)
    decoded = re.sub(r'[A-Za-z0-9+/=]{40,}', '', decoded)
    
    # Extract CJK fragments (>=2 chars)
    fragments = re.findall(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]{2,}', decoded)
    
    if fragments:
        return " ".join(fragments)
    
    return fallback_40090.strip() if fallback_40090 else ""


# ── Main ─────────────────────────────────────────────────────────────

def main():
    if not os.path.exists(DECRYPTED_DB):
        print(f"Error: {DECRYPTED_DB} not found. Run the dbexport plugin first.")
        sys.exit(1)

    src = sqlite3.connect(DECRYPTED_DB)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(str(STATE_DB), timeout=30)
    dst.execute("PRAGMA busy_timeout=60000")
    dst.execute("PRAGMA journal_mode=WAL")
    dst.execute("PRAGMA synchronous=NORMAL")

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

    # Load existing message_ids for dedup (last 60 days)
    cutoff = (datetime.now().timestamp()) - 60 * 86400
    existing = set()
    for r in dst.execute("SELECT message_id FROM corpus_messages WHERE created_at > ?", (cutoff,)):
        existing.add(r[0])
    print(f"Existing corpus message_ids (last 60 days): {len(existing)}")

    # Get group list
    groups = src.execute(
        "SELECT DISTINCT [40021] FROM group_msg_table WHERE [40021] IS NOT NULL AND [40021] != ''"
    ).fetchall()

    total_inserted = 0
    for (gid,) in groups:
        print(f"\n--- Group {gid} ---")

        # Only recover text-type messages: 2=text 8=forward 9=reply 11=@mention
        inserted = 0
        for row in src.execute(
            """SELECT [40001], [40002], [40021], [40050], [40090], [40093], [40800],
                      [40011], [40850], [40100], [40003], [40010]
               FROM group_msg_table
               WHERE [40021] = ? AND [40009] = 1 AND [40011] IN (2, 8, 9, 11)
               ORDER BY [40050] ASC""",
            (gid,),
        ):
            msg_id = str(row[0])
            msg_time = row[3]
            if msg_time <= 0:
                continue
            if msg_id in existing:
                continue

            sender_uin = row[10] or row[1] or 0  # [40003] is real sender UID
            msg_type = row[7] or 0
            reply_to_seq = row[8] or 0
            direction = row[9] or 0
            
            # Extract real text from [40800] protobuf, fallback to [40090]
            raw = str(row[4] or "")  # [40090] kept as content_raw
            text = extract_text(row[6], str(row[4] or ""))  # [40800] primary
            
            if not text.strip():
                continue  # Skip messages with no recoverable text

            # Reply context
            reply_to_id = str(reply_to_seq) if reply_to_seq else ""
            reply_to_text = ""
            
            # @mention detection
            at_targets = "[]"
            if direction == 6:  # @ME
                sender_name = str(row[2] or f"QQ{sender_uin}")
                at_targets = json.dumps([sender_name])
            
            # Sender name: prefer [40021] (display name), fallback to QQ number
            sender_name = str(row[2] or f"QQ{sender_uin}")
            
            try:
                dst.execute(
                    """INSERT INTO corpus_messages
                       (message_id, chat_id, chat_type, group_id, user_id,
                        sender_name, sender_card, content_raw, content_readable,
                        image_descriptions, at_targets, reply_to_id, reply_to_text,
                        is_bot, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (msg_id, str(gid), "group", str(gid),
                     sender_uin, sender_name, "",
                     raw, text,
                     "[]", at_targets, reply_to_id, reply_to_text,
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
            t0_src = src.execute(
                "SELECT MIN([40050]) FROM group_msg_table WHERE [40021]=? AND [40050]>0", (gid,)
            ).fetchone()
            t1_src = src.execute(
                "SELECT MAX([40050]) FROM group_msg_table WHERE [40021]=?", (gid,)
            ).fetchone()
            t0 = datetime.fromtimestamp(t0_src[0]).strftime("%m-%d %H:%M") if t0_src else "?"
            t1 = datetime.fromtimestamp(t1_src[0]).strftime("%m-%d %H:%M") if t1_src else "?"
            print(f"  +{inserted} messages ({t0} ~ {t1})")
        else:
            print(f"  No new messages")

    src.close()
    dst.close()

    print(f"\nTotal inserted: {total_inserted}")
    print(f"Decrypted DB kept for backup: {DECRYPTED_DB}")

if __name__ == "__main__":
    main()
