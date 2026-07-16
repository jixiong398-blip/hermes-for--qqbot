#!/usr/bin/env python3
"""
QQ 聊天记录全量恢复脚本
从解密后的 QQ 数据库提取真实文本，导出 txt + 更新 state.db

用法:
    python qq_chat_restore.py <解密DB路径> [state.db路径]

 示例:
    python qq_chat_restore.py nt_msg_decrypted.db
    python qq_chat_restore.py nt_msg_decrypted.db ~/.hermes/state.db

说明:
    - 解密DB: QQ NT 数据库解密后的文件（nt_msg_decrypted_full_*.db）
    - state.db: Hermes 的 state.db（可选，不传则只导出 txt）
    - 输出: 同目录下的 chat_real_text.txt
    - 支持断点续传（已处理过的 message_id 跳过）
"""

import sqlite3, re, os, sys, shutil
from datetime import datetime

# ── 配置 ──
SRC = sys.argv[1] if len(sys.argv) > 1 else "nt_msg_decrypted_full.db"
STATE = sys.argv[2] if len(sys.argv) > 2 else None
OUT = os.path.join(os.path.dirname(os.path.abspath(SRC)) if os.path.dirname(SRC) else ".", "chat_real_text.txt")

print(f"源DB: {SRC}")
print(f"state.db: {STATE or '不更新'}")
print(f"输出: {OUT}")
print()

# ── 工具函数 ──
def extract_text(t90, t93, blob):
    """从 protobuf BLOB 提取 CJK 文本（更健壮，不依赖字段名）"""
    if not blob:
        return ""
    try:
        raw = blob.decode('utf-8', errors='ignore')
        # 清理 protobuf 二进制噪音
        raw = re.sub(r'[A-F0-9]{16,}\.(jpg|png|gif|jpeg|bmp|webp)', '', raw, flags=re.I)
        raw = re.sub(r'/download\?appid=\d+&fileid=[A-Za-z0-9_\-]+&spec=\d+', '', raw)
        raw = re.sub(r'[A-Fa-f0-9]{32,}', '', raw)
        raw = re.sub(r'[A-Za-z0-9+/=]{40,}', '', raw)
        # 提取 CJK 片段（>=2 字）
        fragments = re.findall(r'[\u4e00-\u9fff]{2,}', raw)
        return ' '.join(fragments) if fragments else ''
    except:
        return ''

def get_reply_text(db, reply_seq):
    """通过回复链获取被回复消息的文本"""
    if not reply_seq or reply_seq <= 0:
        return ""
    rr = db.execute(
        "SELECT [40090],[40093],[40800] FROM group_msg_table WHERE [40002]=? LIMIT 1",
        (reply_seq,)
    ).fetchone()
    if rr:
        return extract_text(rr[0], rr[1], rr[2])
    return ""

# ── 连接数据库 ──
db = sqlite3.connect(SRC)
print(f"消息总数: {db.execute('SELECT COUNT(*) FROM group_msg_table').fetchone()[0]}")
print(f"有效消息(40009=1): {db.execute('SELECT COUNT(*) FROM group_msg_table WHERE [40009]=1').fetchone()[0]}")

# 群列表
groups = sorted(r[0] for r in db.execute(
    "SELECT DISTINCT [40030] FROM group_msg_table WHERE [40009]=1 AND [40030] IS NOT NULL AND [40030]!='' AND [40030]!='0'"
))
print(f"群数: {len(groups)}")

# 群名映射（如果有 state.db）
group_names = {}
if STATE and os.path.exists(STATE):
    sd = sqlite3.connect(STATE)
    for gid, gname in sd.execute("SELECT group_id, group_name FROM groups_registry WHERE group_name != ''"):
        group_names[gid] = gname
    sd.close()

# 已处理的 message_id（如果 state.db 存在，跳过已有的）
existing_ids = set()
if STATE and os.path.exists(STATE):
    sd = sqlite3.connect(STATE)
    existing_ids = {r[0] for r in sd.execute("SELECT message_id FROM corpus_messages WHERE message_id != ''")}
    sd.close()
    print(f"已有消息ID: {len(existing_ids)}")

# ── 全量导出 ──
total, empty, with_reply = 0, 0, 0
print("\n开始导出...")
start = datetime.now()

with open(OUT, 'w', encoding='utf-8-sig') as f:
    f.write(f"# QQ 全量聊天文本 (未过滤)\n")
    f.write(f"# 时间: {datetime.now():%Y-%m-%d %H:%M:%S}\n")
    f.write(f"# 来源: {SRC}\n\n")

    for gid in groups:
        gname = group_names.get(gid, f'群{gid}')
        gcnt = db.execute("SELECT COUNT(*) FROM group_msg_table WHERE [40030]=? AND [40009]=1", (gid,)).fetchone()[0]
        f.write(f"\n{'='*60}\n群: {gname} ({gid}) — {gcnt} 条\n{'='*60}\n")

        for row in db.execute("""
            SELECT [40001],[40002],[40003],[40050],[40011],[40090],[40093],[40800],[40850],[40100]
            FROM group_msg_table WHERE [40030]=? AND [40009]=1 ORDER BY [40050],[40002]
        """, (gid,)):
            mid, msg_seq, uid, ts, mtype, t90, t93, blob, reply_seq, direction = row
            
            # 提取文本
            text = extract_text(t90, t93, blob)
            
            # 回复上下文
            prefix = ""
            if reply_seq and reply_seq > 0:
                r_text = get_reply_text(db, reply_seq)
                if r_text:
                    prefix = f"[回复: {r_text[:60]}] "
                    with_reply += 1
            
            # 格式化
            t = datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
            at_mark = " @ME" if direction == 6 else ""
            display = f"{prefix}{text}" if text else prefix.strip()
            if not display:
                empty += 1
                display = "[空消息]"
            
            f.write(f"[{t}] QQ{uid}{at_mark}: {display}\n")
            total += 1
            
            # 进度
            if total % 20000 == 0:
                elapsed = (datetime.now() - start).total_seconds()
                print(f"  {total} 条 ({elapsed:.0f}s)...")

elapsed = (datetime.now() - start).total_seconds()
size_mb = os.path.getsize(OUT) / 1024 / 1024
print(f"\n文本导出完成!")
print(f"  总消息: {total}")
print(f"  含回复: {with_reply}")
print(f"  空消息: {empty}")
print(f"  输出: {OUT} ({size_mb:.1f} MB)")
print(f"  耗时: {elapsed:.0f}s")
db.close()

# ── 更新 state.db ──
if STATE and os.path.exists(STATE):
    print("\n更新 state.db ...")
    sdb = sqlite3.connect(STATE)
    sdb.execute("PRAGMA journal_mode=WAL")
    sdb.execute("PRAGMA synchronous=NORMAL")
    db = sqlite3.connect(SRC)
    
    updated, start2 = 0, datetime.now()
    for mid, uid, _, _, _, t90, t93, blob, _, _ in db.execute(
        "SELECT [40001],[40002],[40003],[40050],[40011],[40090],[40093],[40800],[40009],[40100] "
        "FROM group_msg_table WHERE [40009]=1"
    ):
        if str(mid) in existing_ids:
            continue  # 跳过已存在的
        text = extract_text(t90, t93, blob)
        if not text:
            continue
        try:
            sdb.execute(
                "UPDATE corpus_messages SET content_readable=?, content_raw=? WHERE message_id=?",
                (text, text, str(mid))
            )
            updated += 1
            if updated % 20000 == 0:
                sdb.commit()
                elapsed2 = (datetime.now() - start2).total_seconds()
                print(f"  {updated} 条 ({elapsed2:.0f}s)...")
        except:
            pass
    
    sdb.commit()
    sdb.close(); db.close()
    elapsed2 = (datetime.now() - start2).total_seconds()
    
    # 备份
    bak = f"{STATE}.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(STATE, bak)
    print(f"state.db 更新: {updated} 条 ({elapsed2:.0f}s)")
    print(f"备份: {bak}")

print("\n全部完成!")
