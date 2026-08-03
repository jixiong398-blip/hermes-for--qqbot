#!/usr/bin/env python3
"""Get recent group chat content for qzone cron jobs.

Data sources (in priority order):
  1. corpus_messages  — permanent archive (preferred, has group_id and clean text)
  2. chat_message_buffer — legacy buffer (deprecated, likely stale)
  3. messages — agent conversation log (fallback, mixed group+private)

Use: python3 get_recent_chat.py [hours]"""

import sqlite3, os, time, sys
from datetime import datetime

db_path = os.getenv("HERMES_STATE_DB", os.path.expanduser("~/.hermes/state.db"))
hours = float(sys.argv[1]) if len(sys.argv) > 1 else 4
now = time.time()
cutoff = now - hours * 3600

db = sqlite3.connect(db_path)

# Strategy 1: corpus_messages (permanent, has group_id, clean text)
rows = db.execute(
    """SELECT created_at, sender_name, content_readable, group_id, is_bot
       FROM corpus_messages 
       WHERE chat_type = 'group' AND is_bot = 0 
         AND created_at > ? 
       ORDER BY created_at DESC 
       LIMIT 30""",
    (cutoff,)
).fetchall()

if rows and len(rows) >= 2:
    print(f"=== 最近{hours}小时群聊记录（共{len(rows)}条）===")
    for ts, sender, content, group_id, is_bot in rows:
        dt = datetime.fromtimestamp(ts).strftime('%m-%d %H:%M')
        if content and len(content) > 3:
            clean = content.replace('[CQ:image,file=', '[图片]').replace('[CQ:face,id=', '[表情]')
            print(f"[{dt}] {sender}: {clean[:200]}")
    db.close()
    sys.exit(0)

# Strategy 2: chat_message_buffer (legacy)
rows = db.execute(
    """SELECT created_at, sender_name, content, chat_id, is_bot
       FROM chat_message_buffer 
       WHERE chat_id != '0' AND is_bot = 0 
         AND created_at > ? 
       ORDER BY created_at DESC 
       LIMIT 30""",
    (cutoff,)
).fetchall()

if rows and len(rows) >= 2:
    print(f"=== 最近{hours}小时群聊记录（共{len(rows)}条）===")
    for ts, sender, content, chat_id, is_bot in rows:
        dt = datetime.fromtimestamp(ts).strftime('%m-%d %H:%M')
        if content and len(content) > 3:
            clean = content.replace('[CQ:image,file=', '[图片]').replace('[CQ:face,id=', '[表情]')
            print(f"[{dt}] {sender}: {clean[:200]}")
    db.close()
    sys.exit(0)

# Strategy 3: messages table (agent conversation fallback)
rows2 = db.execute(
    """SELECT timestamp, substr(content,1,300)
       FROM messages 
       WHERE role='user' AND timestamp > ? 
       ORDER BY timestamp DESC 
       LIMIT 30""",
    (cutoff,)
).fetchall()

if rows2 and len(rows2) >= 2:
    print(f"=== 最近{hours}小时聊天记录（含群聊和私聊，共{len(rows2)}条）===")
    print("⚠️ 注意：以下消息包含群聊和私聊内容，请只选群聊话题发空间")
    for ts, content in rows2:
        dt = datetime.fromtimestamp(ts).strftime('%m-%d %H:%M')
        if content and len(content) > 5 and not content.startswith('[IMPORTANT:'):
            clean = content.replace('[CQ:image,file=', '[图片]').replace('[CQ:face,id=', '[表情]')
            print(f"[{dt}] {clean[:200]}")
    db.close()
    sys.exit(0)

print("NO_DATA")
db.close()
