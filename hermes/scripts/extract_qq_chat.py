#!/usr/bin/env python3
"""Extract real chat text from QQ NT decrypted database - CLEAN version.

Only text-type messages, filtered protobuf extraction.
"""

import sqlite3, re, os, sys
from datetime import datetime

DB = sys.argv[1] if len(sys.argv) > 1 else r"E:\Deepseek\data\hermes_raw\nt_multimodal.db"
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(os.path.dirname(DB), "chat_clean.txt")

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

# Count total
total = db.execute("SELECT COUNT(*) FROM group_msg_table WHERE [40009]=1 AND [40011] IN (2,8,9,11)").fetchone()[0]
print(f"Text-type messages: {total}")

def extract_text_from_blob(blob):
    """Extract ONLY the message text from protobuf BLOB, filtering binary noise.
    
    The protobuf contains: file references (.jpg, .png), download URLs, 
    hex hashes, and the actual message text in CJK fragments.
    We filter out known noise patterns and keep only text-looking fragments.
    """
    if not blob:
        return ""
    try:
        decoded = blob.decode("utf-8", errors="ignore")
    except:
        return ""
    
    # Remove known noise patterns
    # File references: xxxxx.jpg, xxxxx.png
    decoded = re.sub(r'[A-F0-9]{16,}\.(jpg|png|gif|jpeg|bmp|webp)', '', decoded, flags=re.IGNORECASE)
    # Download URLs
    decoded = re.sub(r'/download\?appid=\d+&fileid=[A-Za-z0-9_\-]+&spec=\d+', '', decoded)
    # Hex hash fragments (32+ hex chars)
    decoded = re.sub(r'[A-Fa-f0-9]{32,}', '', decoded)
    # Base64-like strings
    decoded = re.sub(r'[A-Za-z0-9+/=]{40,}', '', decoded)
    # QQ mini-app JSON
    decoded = re.sub(r'"ver":"[^"]*".*?"prompt":"[^"]*"', '', decoded)
    
    # Extract CJK text fragments (2+ chars) - these are the real messages
    fragments = re.findall(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]{2,}', decoded)
    
    if not fragments:
        return ""
    
    # Filter out common short patterns that are NOT messages
    # Like single emoji descriptions, "消息", "图片", etc.
    text = " ".join(fragments)
    
    # If result is just 1-2 chars of non-meaningful text, skip
    meaningful = re.sub(r'\s+', '', text)
    if len(meaningful) <= 1:
        return ""
    
    return text

stats = {"total": 0, "from40800": 0, "from40090": 0, "no_text": 0}

with open(OUT, "w", encoding="utf-8") as f:
    f.write("# QQ Chat Messages - Clean Extraction\n")
    f.write("# Filtered: text/reply/forward/@mention types only\n\n")
    
    rows = db.execute("""
        SELECT [40001],[40002],[40021],[40050],[40011],[40090],[40093],[40800],[40850],[40100]
        FROM group_msg_table
        WHERE [40009]=1 AND [40011] IN (2,8,9,11)
        ORDER BY [40050] ASC
    """).fetchall()
    
    for r in rows:
        stats["total"] += 1
        ts = datetime.fromtimestamp(r[3]).strftime("%Y-%m-%d %H:%M") if r[3] and r[3] > 0 else "????-??-?? ??:??"
        sender = r[2] or "?"
        msg_type = r[4] or 0
        reply_to = r[8] or 0
        direction = r[9] or 0
        
        # Extract from 40800 first
        text = extract_text_from_blob(r[7])
        
        if not text:
            # Fallback: try 40090 if it looks like real text (has punctuation or verbs)
            raw40090 = (r[5] or "").strip()
            if raw40090 and len(raw40090) >= 3:
                text = raw40090
            else:
                raw40093 = (r[6] or "").strip()
                if raw40093 and len(raw40093) >= 3:
                    text = raw40093
        
        if text:
            stats["from40800" if r[7] else "from40090"] += 1
        else:
            stats["no_text"] += 1
            continue
        
        tlabel = {2: "text", 8: "forward", 9: "reply", 11: "@mention"}.get(msg_type, str(msg_type))
        dir_mark = " [ME]" if direction == 0 else (" [@ME]" if direction == 6 else "")
        
        line = f"{ts} | {sender} | {text} | {tlabel}{dir_mark}"
        if reply_to:
            line += f" | reply_to={reply_to}"
        f.write(line + "\n")

db.close()

size_mb = os.path.getsize(OUT) / 1024 / 1024
print(f"\nTotal: {stats['total']}")
print(f"With text: {stats['total'] - stats['no_text']}")
print(f"No text extracted: {stats['no_text']}")
print(f"\nOutput: {OUT}")
print(f"Size: {size_mb:.1f} MB")
