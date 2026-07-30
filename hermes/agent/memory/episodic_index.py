"""
Episode Index (EPI) - 跨会话联想记忆层

  放到 agent/memory/episodic_index.py

为什么需要这一层
--------------------------------------------------------------------
现有四层各有各的天花板:

  STM   存原始对话, 但被 session_id 锁死在本群, 而且 prune_short_term(1.0)
        决定了它只活 24 小时 -- 它是"工作记忆", 不是"往事"。
  LTM   跨群, 但存的是提炼后的事实("某用户喜欢某部动漫"),
        丢掉了"某人在某个场合说过某句话"的质感。
  Buffer / RollingSummary  纯 per-group, 内存态。

用户要的是: 群B 聊到手游 -> 想起群A 有人说过"我最近在玩MyGO手游"。
那是**原始对话片段**的联想, LTM 给不了, STM 够不着。

EPI 就是补这一格: 保留原文的对话片段 + 全局可搜 + 带隐私域。

设计要点
--------------------------------------------------------------------
* 不改 STM 的 session 隔离语义 (STM 仍然只服务本会话的上下文窗口)
* 写入时机挂在既有的 consolidate() 上 -- 每 6 轮一次, 不新增触发路径
* 中文检索用 2-gram 倒排 + IDF 加权, 零外部依赖 (不要 jieba/FTS5/embedding)
* 隐私分级 share_level:
      2 = 可带昵称外传 (群聊, 且 reveal_names=True)
      1 = 只能匿名外传 ("有人提到…")  <- 群聊与私聊的默认值
      0 = 永不外传 (命中敏感线索 / 明示保密)
* 反刷屏: 同一片段对同一目标会话有冷却期, 不会每句话都翻旧账

Usage:
    epi = EpisodeIndex(store)
    epi.index_session("onebot:group:123", "group", stm_entries)
    frags = epi.search("有什么好玩的手游", exclude_session="onebot:group:456")
    block = epi.build_context("有什么好玩的手游", exclude_session="onebot:group:456")
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .short_term import tokenize_for_match

logger = logging.getLogger(__name__)


# ── 调参区 ────────────────────────────────────────────────────────
MAX_TURNS_PER_FRAGMENT = 8
MIN_TURNS_PER_FRAGMENT = 3
MIN_FRAGMENT_CHARS = 30
MAX_FRAGMENT_CHARS = 1200
MAX_LINE_CHARS = 200

DEFAULT_RETENTION_DAYS = 7.0
DEFAULT_MIN_SCORE = 0.35
DEFAULT_LIMIT = 2
DEFAULT_COOLDOWN_SEC = 6 * 3600
RECENCY_HALFLIFE_DAYS = 45.0
MAX_QUERY_TOKENS = 40
DF_STOP_RATIO = 0.30

_STRUCTURAL_PATTERNS = [
    r"\d{6,}",
    r"[\w.+-]+@[\w-]+\.[\w.]+",
]

_STRUCTURAL_RE = re.compile("|".join(_STRUCTURAL_PATTERNS), re.IGNORECASE)
_DIGIT_RUN_RE = re.compile(r"\d{5,}")
_AT_RE = re.compile(r"@[^\s:：,，]{1,20}")


@dataclass
class EpisodeFragment:
    id: int = 0
    session_id: str = ""
    chat_type: str = "group"
    scope: str = "group"
    share_level: int = 1
    text: str = ""
    speakers: List[str] = field(default_factory=list)
    topics: List[str] = field(default_factory=list)
    turn_start: int = 0
    turn_end: int = 0
    start_ts: float = 0.0
    end_ts: float = 0.0
    created_at: float = 0.0
    token_count: int = 0
    surfaced_count: int = 0
    last_surfaced_at: float = 0.0
    score: float = 0.0

    def age_days(self, now: Optional[float] = None) -> float:
        ref = self.end_ts or self.created_at
        if not ref:
            return 0.0
        return max(0.0, ((now or time.time()) - ref) / 86400.0)

    def render(self, now: Optional[float] = None) -> str:
        if self.share_level <= 0:
            return ""
        age = self.age_days(now)
        if age < 1:
            when = "今天"
        elif age < 2:
            when = "昨天"
        elif age < 30:
            when = f"{int(age)}天前"
        else:
            when = f"{int(age / 30)}个月前"
        where = "另一个群" if self.scope == "group" else "私聊里"
        body = self.text if self.share_level >= 2 else _anonymize(self.text)
        return f"[{when} · {where}]\n{body}"


def _anonymize(text: str) -> str:
    lines = []
    for raw in text.split("\n"):
        line = raw
        if "：" in line:
            head, _, tail = line.partition("：")
            if len(head) <= 24 and not head.startswith("我"):
                line = f"有人：{tail}"
        line = _AT_RE.sub("@某人", line)
        line = _DIGIT_RUN_RE.sub("***", line)
        lines.append(line)
    return "\n".join(lines)


class EpisodeIndex:
    def __init__(self, store, retention_days: float = DEFAULT_RETENTION_DAYS,
                 reveal_names: bool = False, enable_dm: bool = True):
        self._store = store
        self.retention_days = retention_days
        self.reveal_names = reveal_names
        self.enable_dm = enable_dm
        self._schema_ready = False

    def _conn(self):
        conn = self._store._get_conn()
        if not self._schema_ready:
            self._ensure_schema(conn)
        return conn

    def _ensure_schema(self, conn):
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS episode_fragments (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      TEXT    NOT NULL,
                chat_type       TEXT    NOT NULL DEFAULT 'group',
                scope           TEXT    NOT NULL DEFAULT 'group',
                share_level     INTEGER NOT NULL DEFAULT 1,
                text            TEXT    NOT NULL,
                speakers        TEXT    NOT NULL DEFAULT '[]',
                topics          TEXT    NOT NULL DEFAULT '[]',
                turn_start      INTEGER NOT NULL DEFAULT 0,
                turn_end        INTEGER NOT NULL DEFAULT 0,
                start_ts        REAL    NOT NULL DEFAULT 0,
                end_ts          REAL    NOT NULL DEFAULT 0,
                created_at      REAL    NOT NULL DEFAULT 0,
                token_count     INTEGER NOT NULL DEFAULT 0,
                dropped_lines   INTEGER NOT NULL DEFAULT 0,
                surfaced_count  INTEGER NOT NULL DEFAULT 0,
                last_surfaced_at REAL   NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_epi_session
                ON episode_fragments(session_id, turn_end);
            CREATE INDEX IF NOT EXISTS idx_epi_created
                ON episode_fragments(created_at);

            CREATE TABLE IF NOT EXISTS episode_tokens (
                token      TEXT    NOT NULL,
                episode_id INTEGER NOT NULL,
                PRIMARY KEY (token, episode_id)
            ) WITHOUT ROWID;
            CREATE INDEX IF NOT EXISTS idx_epi_tok_ep
                ON episode_tokens(episode_id);

            CREATE TABLE IF NOT EXISTS episode_watermark (
                session_id      TEXT PRIMARY KEY,
                last_turn_index INTEGER NOT NULL DEFAULT 0,
                updated_at      REAL    NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS episode_surface_log (
                episode_id     INTEGER NOT NULL,
                target_session TEXT    NOT NULL,
                ts             REAL    NOT NULL,
                PRIMARY KEY (episode_id, target_session)
            ) WITHOUT ROWID;
            """
        )
        conn.commit()
        self._schema_ready = True

    def index_session(self, session_id: str, chat_type: str = "group",
                      entries: Optional[Sequence[Any]] = None) -> int:
        if not entries:
            return 0
        conn = self._conn()
        row = conn.execute(
            "SELECT last_turn_index FROM episode_watermark WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        watermark = int(row[0]) if row else 0

        fresh = [
            e for e in entries
            if int(getattr(e, "turn_index", 0) or 0) > watermark
            and (getattr(e, "content", "") or "").strip()
        ]
        if len(fresh) < MIN_TURNS_PER_FRAGMENT:
            return 0

        fresh.sort(key=lambda e: int(getattr(e, "turn_index", 0) or 0))
        created = 0
        for i in range(0, len(fresh), MAX_TURNS_PER_FRAGMENT):
            chunk = fresh[i:i + MAX_TURNS_PER_FRAGMENT]
            if len(chunk) < MIN_TURNS_PER_FRAGMENT and created > 0:
                break
            if self._store_fragment(conn, session_id, chat_type, chunk):
                created += 1

        last_idx = int(getattr(fresh[-1], "turn_index", 0) or 0)
        conn.execute(
            "INSERT OR REPLACE INTO episode_watermark "
            "(session_id, last_turn_index, updated_at) VALUES (?, ?, ?)",
            (session_id, last_idx, time.time()),
        )
        conn.commit()
        if created:
            logger.info("[EPI] indexed %d fragment(s) from %s (turns %s..%s)",
                        created, session_id, watermark + 1, last_idx)
        return created

    def _store_fragment(self, conn, session_id: str, chat_type: str,
                        chunk: Sequence[Any]) -> bool:
        lines: List[str] = []
        speakers: List[str] = []
        topics: List[str] = []
        raw_all: List[str] = []

        for e in chunk:
            content = (getattr(e, "content", "") or "").strip()
            if not content:
                continue
            raw_all.append(content)
            role = getattr(e, "role", "user")
            if role == "assistant":
                name = "我"
            else:
                name = (getattr(e, "speaker_name", "") or "").strip() or "群友"

            if _STRUCTURAL_RE.search(content):
                continue

            if name != "我" and name not in speakers:
                speakers.append(name)
            for t in (getattr(e, "topics", None) or []):
                if t not in topics:
                    topics.append(t)
            lines.append(f"{name}：{content[:MAX_LINE_CHARS]}")

        text = "\n".join(lines)[:MAX_FRAGMENT_CHARS]
        if len(re.sub(r"\s+", "", text)) < MIN_FRAGMENT_CHARS:
            return False

        scope = "dm" if chat_type == "dm" else "group"

        try:
            from plugins.platforms.onebot.semantic_judge import judge_episode_privacy_sync
            privacy = judge_episode_privacy_sync(text)
            share_level = privacy.get("share_level", 1)
            if share_level == 0:
                logger.info("[EPI] sealed fragment from %s (LLM: %s)",
                            session_id, privacy.get("reason", ""))
                return False
            if scope == "dm" and share_level > 1:
                share_level = 1
        except Exception:
            share_level = self._decide_share_level(scope)

        tokens = tokenize_for_match(text, limit=256)
        if len(tokens) < 4:
            return False

        dropped = max(0, len(chunk) - len(lines))

        ts_list = [float(getattr(e, "created_at", 0) or 0) for e in chunk]
        ts_list = [t for t in ts_list if t > 0]
        turn_idx = [int(getattr(e, "turn_index", 0) or 0) for e in chunk]
        now = time.time()

        cur = conn.execute(
            "INSERT INTO episode_fragments "
            "(session_id, chat_type, scope, share_level, text, speakers, topics,"
            " turn_start, turn_end, start_ts, end_ts, created_at, token_count,"
            " dropped_lines) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                session_id, chat_type, scope, share_level, text,
                json.dumps(speakers, ensure_ascii=False),
                json.dumps(topics[:20], ensure_ascii=False),
                min(turn_idx) if turn_idx else 0,
                max(turn_idx) if turn_idx else 0,
                min(ts_list) if ts_list else now,
                max(ts_list) if ts_list else now,
                now, len(tokens), dropped,
            ),
        )
        ep_id = cur.lastrowid
        conn.executemany(
            "INSERT OR IGNORE INTO episode_tokens (token, episode_id) VALUES (?, ?)",
            [(t, ep_id) for t in tokens],
        )
        return True

    def _decide_share_level(self, scope: str) -> int:
        if scope == "dm":
            return 1 if self.enable_dm else 0
        return 2 if self.reveal_names else 1

    def search(self, query: str,
               exclude_session: Optional[str] = None,
               limit: int = DEFAULT_LIMIT,
               min_score: float = DEFAULT_MIN_SCORE,
               cooldown_sec: float = DEFAULT_COOLDOWN_SEC,
               include_own_session: bool = False) -> List[EpisodeFragment]:
        q_tokens = tokenize_for_match(query, limit=MAX_QUERY_TOKENS)
        if len(q_tokens) < 2:
            return []

        conn = self._conn()
        total = conn.execute("SELECT COUNT(*) FROM episode_fragments").fetchone()[0]
        if not total:
            return []

        ph = ",".join("?" * len(q_tokens))
        df_rows = conn.execute(
            f"SELECT token, COUNT(*) FROM episode_tokens "
            f"WHERE token IN ({ph}) GROUP BY token",
            list(q_tokens),
        ).fetchall()
        if not df_rows:
            return []

        df_cap = max(2, int(total * DF_STOP_RATIO))
        idf: Dict[str, float] = {}
        for row in df_rows:
            token, df = row[0], int(row[1])
            if df > df_cap:
                continue
            idf[token] = math.log(1.0 + total / (1.0 + df))
        if not idf:
            return []

        norm = sum(idf.values()) or 1.0
        ph2 = ",".join("?" * len(idf))
        rows = conn.execute(
            f"SELECT episode_id, token FROM episode_tokens WHERE token IN ({ph2})",
            list(idf.keys()),
        ).fetchall()

        raw: Dict[int, float] = defaultdict(float)
        hits: Dict[int, int] = defaultdict(int)
        for row in rows:
            ep_id, token = int(row[0]), row[1]
            raw[ep_id] += idf.get(token, 0.0)
            hits[ep_id] += 1
        if not raw:
            return []

        top_ids = sorted(raw, key=lambda k: -raw[k])[:40]
        ph3 = ",".join("?" * len(top_ids))
        frag_rows = conn.execute(
            f"SELECT f.id, f.session_id, f.chat_type, f.scope, f.share_level, f.text,"
            f" f.speakers, f.topics, f.turn_start, f.turn_end, f.start_ts, f.end_ts,"
            f" f.created_at, f.token_count, f.surfaced_count, f.last_surfaced_at,"
            f" s.ts "
            f"FROM episode_fragments f "
            f"LEFT JOIN episode_surface_log s "
            f"  ON s.episode_id = f.id AND s.target_session = ? "
            f"WHERE f.id IN ({ph3}) AND f.share_level >= 1",
            [exclude_session or ""] + top_ids,
        ).fetchall()

        now = time.time()
        out: List[EpisodeFragment] = []
        for r in frag_rows:
            ep_id = int(r[0])
            sess = r[1]
            if not include_own_session and exclude_session and sess == exclude_session:
                continue
            scope = r[3]
            if scope == "dm" and not self.enable_dm:
                continue
            last_surface = float(r[16] or 0)
            if last_surface and (now - last_surface) < cooldown_sec:
                continue

            tok_count = int(r[13] or 1)
            matched = hits.get(ep_id, 0)
            recall_q = raw[ep_id] / norm
            precision_boost = 0.75 + 0.25 * min(1.0, 3.0 * matched / max(tok_count, 1))
            score = recall_q * precision_boost

            frag = EpisodeFragment(
                id=ep_id, session_id=sess, chat_type=r[2], scope=scope,
                share_level=int(r[4]), text=r[5],
                speakers=_loads(r[6]), topics=_loads(r[7]),
                turn_start=int(r[8] or 0), turn_end=int(r[9] or 0),
                start_ts=float(r[10] or 0), end_ts=float(r[11] or 0),
                created_at=float(r[12] or 0), token_count=tok_count,
                surfaced_count=int(r[14] or 0), last_surfaced_at=last_surface,
            )
            rec = 0.5 ** (frag.age_days(now) / RECENCY_HALFLIFE_DAYS)
            frag.score = score * (0.85 + 0.15 * rec)

            if frag.score >= min_score:
                out.append(frag)

        out.sort(key=lambda f: -f.score)
        return out[:limit]

    def build_context(self, query: str, exclude_session: Optional[str] = None,
                      limit: int = DEFAULT_LIMIT, max_chars: int = 900,
                      mark: bool = True) -> str:
        frags = self.search(query, exclude_session=exclude_session, limit=limit)
        if not frags:
            return ""

        now = time.time()
        lines = [
            "### 别处的印象",
            "(不是本群的对话, 是你在别的地方听来的。可以自然地联想提起, "
            "但**不要说出是谁说的、在哪个群说的**。不确定就别硬提。)",
        ]
        used = 0
        surfaced: List[int] = []
        for f in frags:
            block = f.render(now)
            if not block:
                continue
            if used + len(block) > max_chars:
                break
            lines.append(block)
            used += len(block)
            surfaced.append(f.id)

        if not surfaced:
            return ""
        if mark and exclude_session:
            self.mark_surfaced(surfaced, exclude_session)
        return "\n\n".join(lines)

    def mark_surfaced(self, episode_ids: Iterable[int], target_session: str):
        ids = [int(i) for i in episode_ids]
        if not ids or not target_session:
            return
        conn = self._conn()
        now = time.time()
        conn.executemany(
            "INSERT OR REPLACE INTO episode_surface_log "
            "(episode_id, target_session, ts) VALUES (?, ?, ?)",
            [(i, target_session, now) for i in ids],
        )
        conn.executemany(
            "UPDATE episode_fragments SET surfaced_count = surfaced_count + 1, "
            "last_surfaced_at = ? WHERE id = ?",
            [(now, i) for i in ids],
        )
        conn.commit()

    def prune(self, retention_days: Optional[float] = None) -> int:
        days = retention_days if retention_days is not None else self.retention_days
        cutoff = time.time() - days * 86400.0
        conn = self._conn()
        cur = conn.execute("DELETE FROM episode_fragments WHERE created_at < ?", (cutoff,))
        removed = cur.rowcount or 0
        if removed:
            conn.execute(
                "DELETE FROM episode_tokens WHERE episode_id NOT IN "
                "(SELECT id FROM episode_fragments)"
            )
            conn.execute(
                "DELETE FROM episode_surface_log WHERE episode_id NOT IN "
                "(SELECT id FROM episode_fragments)"
            )
        conn.execute("DELETE FROM episode_surface_log WHERE ts < ?",
                     (time.time() - 30 * 86400.0,))
        conn.commit()
        return removed

    def forget_session(self, session_id: str) -> int:
        conn = self._conn()
        cur = conn.execute("DELETE FROM episode_fragments WHERE session_id = ?",
                           (session_id,))
        conn.execute(
            "DELETE FROM episode_tokens WHERE episode_id NOT IN "
            "(SELECT id FROM episode_fragments)"
        )
        conn.execute("DELETE FROM episode_watermark WHERE session_id = ?", (session_id,))
        conn.commit()
        return cur.rowcount or 0

    def stats(self) -> Dict[str, Any]:
        conn = self._conn()
        row = conn.execute(
            "SELECT COUNT(*), "
            "       SUM(CASE WHEN scope='dm' THEN 1 ELSE 0 END), "
            "       SUM(CASE WHEN share_level=0 THEN 1 ELSE 0 END), "
            "       SUM(surfaced_count), "
            "       SUM(dropped_lines) "
            "FROM episode_fragments"
        ).fetchone()
        tok = conn.execute("SELECT COUNT(*) FROM episode_tokens").fetchone()[0]
        sessions = conn.execute(
            "SELECT COUNT(DISTINCT session_id) FROM episode_fragments").fetchone()[0]
        return {
            "fragments": int(row[0] or 0),
            "dm_fragments": int(row[1] or 0),
            "sealed_fragments": int(row[2] or 0),
            "redacted_lines": int(row[4] or 0),
            "total_surfaced": int(row[3] or 0),
            "postings": int(tok or 0),
            "sessions": int(sessions or 0),
            "retention_days": self.retention_days,
            "reveal_names": self.reveal_names,
            "dm_enabled": self.enable_dm,
        }


def _loads(raw) -> List[str]:
    try:
        val = json.loads(raw) if raw else []
        return val if isinstance(val, list) else []
    except Exception:
        return []
