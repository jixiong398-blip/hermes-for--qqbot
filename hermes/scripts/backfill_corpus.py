#!/usr/bin/env python3
"""Backfill missing messages into corpus_messages using NapCat HTTP API.

Reads the latest corpus_messages timestamp per group, then fetches newer
messages from NapCat's get_group_msg_history API and inserts them.
"""

import sqlite3
import json
import time
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

NAPCAT_HTTP = os.getenv("ONEBOT_HTTP_URL", "http://127.0.0.1:3000")
ACCESS_TOKEN = os.getenv("ONEBOT_ACCESS_TOKEN", "")
STATE_DB = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))) / "state.db"

def napcat_api(action: str, params: dict) -> dict:
    req = Request(
        f"{NAPCAT_HTTP}/{action}",
        data=json.dumps(params).encode(),
        headers={
            "Authorization": f"Bearer {ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except URLError as e:
        print(f"  API error: {e}")
        return {"retcode": -1, "data": {}}

def get_groups() -> list:
    r = napcat_api("get_group_list", {})
    return r.get("data", []) if r.get("retcode") == 0 else []

def get_msg_history(group_id: int, count: int = 200, message_seq: int = None) -> list:
    params = {"group_id": group_id, "count": count}
    if message_seq:
        params["message_seq"] = message_seq
    r = napcat_api("get_group_msg_history", params)
    msgs = r.get("data", {}).get("messages", []) if r.get("retcode") == 0 else []
    if not msgs:
        msgs = r.get("data", {}).get("message", [])
    return msgs

def main():
    db = sqlite3.connect(str(STATE_DB), timeout=30)
    db.execute("PRAGMA busy_timeout=60000")

    # Ensure tables exist
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
        CREATE INDEX IF NOT EXISTS idx_corpus_chat_time ON corpus_messages(chat_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_corpus_group ON corpus_messages(group_id, created_at);
    """)
    db.commit()

    # Get existing message_ids for dedup (only recent ones to keep it fast)
    existing_ids = set()
    cutoff = time.time() - 30 * 86400  # last 30 days
    for row in db.execute(
        "SELECT message_id FROM corpus_messages WHERE created_at > ?", (cutoff,)
    ):
        existing_ids.add(row[0])

    print(f"Existing message_ids in last 30 days: {len(existing_ids)}")
    print()

    total_inserted = 0
    groups = get_groups()
    print(f"Found {len(groups)} group(s)")
    print()

    for g in groups:
        gid = g["group_id"]
        gname = g["group_name"]
        print(f"--- Group {gid} ({gname}) ---")

        # Find latest timestamp for this group in corpus_messages
        row = db.execute(
            "SELECT MAX(created_at) FROM corpus_messages WHERE group_id = ?",
            (str(gid),),
        ).fetchone()
        latest_ts = row[0] if row and row[0] else 0
        latest_str = (
            datetime.fromtimestamp(latest_ts).strftime("%Y-%m-%d %H:%M:%S")
            if latest_ts > 0
            else "N/A"
        )
        print(f"  Latest in corpus: {latest_str}")

        inserted = 0
        fetch_count = 500
        max_pages = 50
        next_seq = None
        cutoff_ts = 1781942400  # 2026-06-21 00:00 UTC
        total_fetched = 0

        for page in range(max_pages):
            msgs = get_msg_history(gid, fetch_count, message_seq=next_seq)
            if not msgs:
                break
            if page == 0:
                print(f"  Page 1: fetched {len(msgs)} msg(s)")

            page_inserted = 0
            oldest_time = None
            oldest_seq = None
            reached_cutoff = False

            for msg in msgs:
                msg_id = str(msg.get("message_id", "") or msg.get("real_id", ""))
                msg_time = msg.get("time", 0)
                msg_seq = msg.get("message_seq", 0)

                if oldest_time is None or msg_time < oldest_time:
                    oldest_time = msg_time
                    oldest_seq = msg_seq

                # Stop pagination when we reach the cutoff date
                if msg_time <= cutoff_ts:
                    reached_cutoff = True
                    break

                if not msg_id or msg_id in existing_ids:
                    continue

                sender = msg.get("sender", {})
                user_id = msg.get("user_id", 0)
                sender_name = sender.get("card") or sender.get("nickname") or f"QQ{user_id}"
                raw = msg.get("raw_message", "") or ""
                segments = msg.get("message", [])
                if isinstance(segments, list):
                    parts = []
                    for seg in segments:
                        t = seg.get("type", "")
                        d = seg.get("data", {})
                        if t == "text":
                            parts.append(d.get("text", ""))
                        elif t == "image":
                            parts.append("[图片]")
                        elif t == "at":
                            parts.append(f"@QQ{d.get('qq','')}")
                        elif t == "face":
                            parts.append("[表情]")
                        elif t == "file":
                            parts.append(f"[文件:{d.get('file','')[:20]}]")
                        elif t == "video":
                            parts.append("[视频]")
                        elif t == "voice":
                            parts.append("[语音]")
                        else:
                            pass
                    readable = "".join(parts) if parts else raw

                at_targets = json.dumps(
                    [d.get("qq", "") for seg in segments
                     if isinstance(seg, dict) and seg.get("type") == "at" and seg.get("data", {}).get("qq")]
                )
                reply_to_id = ""
                for seg in segments:
                    if isinstance(seg, dict) and seg.get("type") == "reply":
                        reply_to_id = str(seg.get("data", {}).get("id", ""))
                        break

                is_bot = 1 if str(user_id) == str(msg.get("self_id", "")) else 0
                try:
                    db.execute(
                        """INSERT INTO corpus_messages
                           (message_id, chat_id, chat_type, group_id, user_id,
                            sender_name, sender_card, content_raw, content_readable,
                            image_descriptions, at_targets, reply_to_id, reply_to_text,
                            is_bot, created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (msg_id, str(gid), "group", str(gid),
                         user_id, sender_name, sender.get("card", ""),
                         raw, readable,
                         "[]", at_targets, reply_to_id, "",
                         is_bot, float(msg_time)),
                    )
                    page_inserted += 1
                    existing_ids.add(msg_id)
                except Exception as e:
                    if "UNIQUE" not in str(e):
                        print(f"  Insert error: {e}")

            inserted += page_inserted
            db.commit()

            page_time = datetime.fromtimestamp(oldest_time).strftime('%m-%d %H:%M') if oldest_time else '?'
            print(f"    Page {page+1}: +{page_inserted} inserted, oldest={page_time}, total={total_fetched+len(msgs)} fetched")

            if oldest_time == -1:
                break
            if reached_cutoff:
                print(f"    Reached 06-21 cutoff, stopping")
                break
            if oldest_seq:
                next_seq = oldest_seq
                total_fetched += len(msgs)

        if inserted > 0:
            db.commit()
            print(f"  Inserted {inserted} new message(s)")
            total_inserted += inserted
        else:
            print(f"  No new messages to insert")

    db.close()
    print()
    print(f"DONE. Total inserted: {total_inserted}")

if __name__ == "__main__":
    main()

