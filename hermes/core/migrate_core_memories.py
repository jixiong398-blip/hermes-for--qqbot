import sqlite3
import time
import re
from pathlib import Path

DB_PATH = Path.home() / ".hermes" / "memory_store.db"
SOUL_PATH = Path.home() / ".hermes" / "SOUL.md"

MIGRATIONS = [
    ("事件", "我记得某位群友纠正过我，高考早就考完了", None),
    ("事件", "我记得多手的怪物6月7号就考完高考了", 1749254400),
    ("人物", "我记得圆明斗姥天尊考完四级后打算对比一堆云服务器平台", None),
    ("人物", "我记得圆明斗姥天尊说过MC服务器16g配11G流量大概30块一个月，正常要60", None),
    ("人物", "我记得圆明斗姥天尊主要是玩游戏搞的服务器，云服务器方面知识不太够", None),
    ("偏好", "我记得雨心喜欢蝶团的食材准备图", None),
    ("人物", "我记得雪凪听说BW人很挤，想去的展位很难排到", None),
    ("事件", "我记得咖啡今年暑假打算去上海", None),
    ("事件", "我记得某位群友去过海边城市，坐的硬卧", None),
    ("偏好", "我记得雪凪一般不考虑高铁通勤超过两个半小时的行程", None),
    ("事件", "我记得雪凪只出过一次省，去上海玩了两天花了300块", None),
    ("人物", "我记得rana想弹吉他", None),
    ("人物", "我记得nyamu是鼓手", None),
    ("人物", "我记得某位群友会弹吉他", None),
    ("事件", "我记得神巫堆弁天提过声优反田在2018年时还是高中生", None),
]


def ensure_table(conn):
    conn.executescript("""
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
    """)
    conn.commit()


def migrate(conn):
    now = time.time()
    inserted = 0
    for category, content, occurred_at in MIGRATIONS:
        existing = conn.execute(
            "SELECT id FROM core_memories WHERE content = ? AND active = 1", (content,)
        ).fetchone()
        if existing:
            continue
        conn.execute(
            "INSERT INTO core_memories (category, content, source, occurred_at, "
            "created_at, updated_at, active) VALUES (?, ?, 'migrated', ?, ?, ?, 1)",
            (category, content, occurred_at or now, now, now),
        )
        inserted += 1
    conn.commit()
    return inserted


def strip_soul(soul_text):
    pattern = r'\n## 我的记忆\n.*$'
    stripped = re.sub(pattern, '', soul_text, flags=re.DOTALL)
    return stripped.rstrip() + '\n'


def main():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    ensure_table(conn)
    inserted = migrate(conn)
    conn.close()

    soul_text = SOUL_PATH.read_text(encoding="utf-8")
    if "## 我的记忆" in soul_text:
        new_soul = strip_soul(soul_text)
        SOUL_PATH.write_text(new_soul, encoding="utf-8")
        print(f"Stripped '## 我的记忆' from SOUL.md")
    else:
        print("SOUL.md already stripped")

    print(f"Migrated {inserted} core memories (deduplicated)")


if __name__ == "__main__":
    main()
