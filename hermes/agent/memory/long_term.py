"""
Long-Term Memory (LTM) v2 — Ebbinghaus decay, reconsolidation, supersede.

Closed-loop memory:
  L1: Recall triggers reconsolidation → strengthens via Ebbinghaus formula
  L2: Correction → supersede (soft-delete old, write new, link via edge)
  L4: Salience-based decay modifier (high salience decays slower)
  L5: recall_strength < 0.3 → context marked [uncertain]
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

from .store import MemoryStore, LongTermEntry

logger = logging.getLogger(__name__)

CATEGORIES = [
    "user_profile",
    "user_preference",
    "agent_identity",
    "knowledge",
    "decision",
    "relationship",
    "coding",
    "sticker",
    "general",
]

EBBINGHAUS_LAMBDA = 0.02
RECONSOLIDATION_BOOST = 0.15
CORRECTION_SALIENCE_MIN = 0.8
RECALL_DELETE_THRESHOLD = 0.05
UNCERTAIN_THRESHOLD = 0.3

CN_CORRECTION_PATTERNS = re.compile(
    r"搞错了|不对|不是.*是|记错了|更正|纠正|弄混了|你错了|说错了|哪有|早就.*了"
)

CN_RELATIVE_DATE = re.compile(
    r"(明天|后天|大后天|昨天|前天|大前天|下周[一二三四五六日天]?|下下周|上周|本周|这周|"
    r"下个月|上个月|这个月|明年|去年|今年|(\d+)天后|(\d+)天后|(\d+)周后)",
)

DATE_WEEKDAY_MAP = {
    "一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6,
}


class LongTermMemory:
    """Manages persistent long-term facts with Ebbinghaus decay."""

    def __init__(self, store: MemoryStore):
        self._store = store

    # ── Core CRUD ──────────────────────────────────────────────

    def add_fact(self, category: str, key: str, value: str,
                 tags: Optional[List[str]] = None,
                 confidence: float = 0.5,
                 session_id: Optional[str] = None,
                 source_user_id: str = "",
                 source_message_ts: str = "",
                 source_context: str = "",
                 derivation: str = "direct",
                 memory_type: str = "semantic",
                 type_data: Optional[dict] = None,
                 salience: float = 0.5) -> int:
        if category not in CATEGORIES:
            category = "general"

        td = type_data or {}
        td.setdefault("subcategory", category)
        td.setdefault("key", key)

        now = datetime.now(timezone.utc)
        entry = LongTermEntry(
            category=category,
            key=key,
            value=value,
            tags=tags or [],
            confidence=min(1.0, max(0.0, confidence)),
            source_session_ids=[session_id] if session_id else [],
            ttl_days=None,
            created_at=now.timestamp(),
            updated_at=now.timestamp(),
        )
        eid = self._store.upsert_long_term(entry)
        if eid:
            self._store._get_conn().execute(
                """UPDATE long_term_entries SET
                    memory_type=?, type_data=?, salience=?, recall_strength=1.0,
                    derivation=?, source_user_id=?, source_message_ts=?, source_context=?
                   WHERE id=?""",
                (memory_type, json.dumps(td, ensure_ascii=False), salience,
                 derivation, source_user_id, source_message_ts, source_context, eid),
            )
            self._store._get_conn().commit()
        return eid

    def get_fact(self, category: str, key: str) -> Optional[LongTermEntry]:
        results = self._store.get_long_term(category=category, limit=1)
        for r in results:
            if r.key == key:
                return r
        return None

    def get_category(self, category: str, limit: int = 50) -> List[LongTermEntry]:
        return self._store.get_long_term(category=category, limit=limit)

    def search(self, query: str, limit: int = 10) -> List[LongTermEntry]:
        results = self._store.search_long_term(query, limit)
        for r in results:
            self.reconsolidate(r.id)
        return results

    def get_all(self, limit: int = 100) -> List[LongTermEntry]:
        return self._store.get_long_term(limit=limit)

    def get_high_confidence(self, min_confidence: float = 0.5) -> List[LongTermEntry]:
        return self._store.get_ltm_by_confidence(min_confidence)

    def get_by_id(self, entry_id: int) -> Optional[LongTermEntry]:
        conn = self._store._get_conn()
        row = conn.execute(
            "SELECT * FROM long_term_entries WHERE id=?", (entry_id,)
        ).fetchone()
        if row:
            return self._store._row_to_long_term(row)
        return None

    def update_confidence(self, entry_id: int, delta: float):
        conn = self._store._get_conn()
        row = conn.execute(
            "SELECT confidence FROM long_term_entries WHERE id=?", (entry_id,)
        ).fetchone()
        if row:
            new_conf = min(1.0, max(0.0, row["confidence"] + delta))
            conn.execute(
                "UPDATE long_term_entries SET confidence=?, updated_at=? WHERE id=?",
                (new_conf, time.time(), entry_id),
            )
            conn.commit()

    def forget_fact(self, entry_id: int, reason: str = ""):
        conn = self._store._get_conn()
        conn.execute(
            "UPDATE long_term_entries SET active=0, deleted_at=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), entry_id),
        )
        conn.commit()

    def delete_fact(self, entry_id: int):
        self._store.delete_long_term(entry_id)

    # ── L1: Ebbinghaus Forgetting ──────────────────────────────

    @staticmethod
    def compute_recall_strength(salience: float, reconsolidation_count: int,
                                 last_recalled_at: Optional[str],
                                 created_at: float) -> float:
        S = max(0.1, salience)
        R = min(1.0, 0.7 + 0.3 * math.log(1 + reconsolidation_count))

        ref_str = last_recalled_at or ""
        if ref_str and ref_str != "":
            try:
                ref_dt = datetime.fromisoformat(ref_str)
                ref_ts = ref_dt.timestamp()
            except (ValueError, TypeError):
                ref_ts = created_at
        else:
            ref_ts = created_at

        t_days = (time.time() - ref_ts) / 86400.0
        t_days = max(0, t_days)
        strength = S * R * math.exp(-EBBINGHAUS_LAMBDA * t_days / S)
        return max(0.0, min(1.0, strength))

    def reconsolidate(self, entry_id: int,
                       evidence: Optional[dict] = None,
                       is_boost: bool = True) -> Optional[dict]:
        conn = self._store._get_conn()
        row = conn.execute(
            """SELECT id, category, key, value, tags, confidence, source_session_ids,
               ttl_days, created_at, updated_at,
               salience, recall_strength, reconsolidation_count,
               last_recalled_at, memory_type, type_data,
               source_user_id, source_message_ts, source_context,
               derivation, supersedes_id, active
               FROM long_term_entries WHERE id=?""",
            (entry_id,),
        ).fetchone()
        if not row or not row["active"]:
            return None

        now_iso = datetime.now(timezone.utc).isoformat()

        if is_boost:
            S = max(0.1, row["salience"])
            old_strength = row["recall_strength"]
            boost = RECONSOLIDATION_BOOST * (1.0 - old_strength)
            new_strength = min(1.0, old_strength + boost)
            new_count = row["reconsolidation_count"] + 1
            final_strength = LongTermMemory.compute_recall_strength(
                S, new_count, now_iso, row["created_at"]
            )
        else:
            new_count = row["reconsolidation_count"]
            final_strength = LongTermMemory.compute_recall_strength(
                row["salience"], new_count, row["last_recalled_at"], row["created_at"]
            )

        conn.execute(
            """UPDATE long_term_entries SET
                recall_strength=?, reconsolidation_count=?, last_recalled_at=?
               WHERE id=?""",
            (round(final_strength, 4), new_count, now_iso, entry_id),
        )

        if evidence and evidence.get("is_correction"):
            return self._apply_correction(conn, row, evidence, now_iso)

        conn.commit()
        return None

    # ── L2: Correction → Supersede ─────────────────────────────

    def _apply_correction(self, conn, old_row, evidence: dict,
                           now_iso: str) -> dict:
        target = evidence.get("target_memory") or {}
        new_value = evidence.get("new_value", "")
        new_confidence = evidence.get("new_confidence", 0.85)
        correction_ts = evidence.get("correction_ts", now_iso)

        if new_value and target.get("id") == old_row["id"]:
            resolved = self.resolve_relative_dates(new_value, correction_ts)
        else:
            resolved = new_value

        old_salience = old_row["salience"]
        new_salience = max(old_salience, CORRECTION_SALIENCE_MIN)

        old_type_data = json.loads(old_row["type_data"] or "{}")
        new_type_data = dict(old_type_data)
        new_type_data["previous"] = old_row["value"]
        new_type_data["resolved_dates"] = []

        if resolved != old_row["value"]:
            dates = self._extract_dates(resolved)
            if dates:
                new_type_data["resolved_dates"] = dates

        conn.execute(
            "UPDATE long_term_entries SET active=0, deleted_at=? WHERE id=?",
            (now_iso, old_row["id"]),
        )

        conn.execute(
            """INSERT INTO long_term_entries
               (category, key, value, tags, confidence, source_session_ids,
                ttl_days, created_at, updated_at,
                memory_type, type_data, salience, recall_strength,
                derivation, supersedes_id, active,
                source_user_id, source_message_ts, source_context)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                old_row["category"], old_row["key"], resolved,
                old_row["tags"], new_confidence,
                old_row["source_session_ids"], old_row["ttl_days"],
                time.time(), time.time(),
                old_row["memory_type"], json.dumps(new_type_data, ensure_ascii=False),
                new_salience, 1.0,
                "corrected", old_row["id"], 1,
                old_row["source_user_id"] if "source_user_id" in old_row.keys() else "",
                correction_ts,
                old_row["source_context"] if "source_context" in old_row.keys() else "",
            ),
        )
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        conn.execute(
            """INSERT OR IGNORE INTO memory_edges (src_id, dst_id, relation, weight, created_at)
               VALUES (?, ?, 'corrected_by', 1.0, ?)""",
            (new_id, old_row["id"], now_iso),
        )
        conn.commit()
        return {"new_id": new_id, "old_id": old_row["id"], "value": resolved}

    def supersede_fact(self, old_id: int, new_value: str,
                        new_confidence: float = 0.85,
                        correction_ts: str = "",
                        source_user_id: str = "") -> Optional[dict]:
        now_iso = datetime.now(timezone.utc).isoformat()
        return self.reconsolidate(old_id, evidence={
            "is_correction": True,
            "target_memory": {"id": old_id},
            "new_value": new_value,
            "new_confidence": new_confidence,
            "correction_ts": correction_ts or now_iso,
        }, is_boost=False)

    # ── Date Resolution ────────────────────────────────────────

    def resolve_relative_dates(self, text: str, reference_ts: str = "") -> str:
        if not reference_ts:
            ref = datetime.now(timezone.utc)
        else:
            try:
                ref = datetime.fromisoformat(reference_ts)
                if ref.tzinfo is None:
                    ref = ref.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                ref = datetime.now(timezone.utc)

        def _replace(m):
            token = m.group(1)
            today = ref.date()
            if token == "明天":
                d = today + timedelta(days=1)
            elif token == "后天":
                d = today + timedelta(days=2)
            elif token == "大后天":
                d = today + timedelta(days=3)
            elif token == "昨天":
                d = today - timedelta(days=1)
            elif token == "前天":
                d = today - timedelta(days=2)
            elif token == "大前天":
                d = today - timedelta(days=3)
            elif token.startswith("下周"):
                wd = token[2:]
                target_wd = DATE_WEEKDAY_MAP.get(wd, today.weekday())
                days_ahead = (target_wd - today.weekday() + 7) % 7 or 7
                d = today + timedelta(days=days_ahead)
            elif token.startswith("上周"):
                wd = token[2:]
                target_wd = DATE_WEEKDAY_MAP.get(wd, today.weekday())
                days_behind = (today.weekday() - target_wd + 7) % 7 or 7
                d = today - timedelta(days=days_behind)
            elif token == "本周" or token == "这周":
                d = today
            elif token == "下个月":
                m = today.month + 1
                y = today.year
                if m > 12:
                    m = 1
                    y += 1
                d = today.replace(year=y, month=m, day=1)
            elif token == "上个月":
                m = today.month - 1
                y = today.year
                if m < 1:
                    m = 12
                    y -= 1
                d = today.replace(year=y, month=m, day=1)
            elif token == "这个月":
                d = today.replace(day=1)
            elif token == "明年":
                d = today.replace(year=today.year + 1)
            elif token == "去年":
                d = today.replace(year=today.year - 1)
            elif token == "今年":
                d = today
            else:
                return token
            return d.isoformat()

        return CN_RELATIVE_DATE.sub(_replace, text)

    def _extract_dates(self, text: str) -> List[str]:
        matches = re.findall(r'\d{4}-\d{2}-\d{2}', text)
        return list(set(matches))

    # ── L5: Uncertainty Marking ────────────────────────────────

    def is_uncertain(self, entry_id: int) -> bool:
        conn = self._store._get_conn()
        row = conn.execute(
            "SELECT recall_strength FROM long_term_entries WHERE id=? AND active=1",
            (entry_id,),
        ).fetchone()
        if not row:
            return True
        return (row["recall_strength"] or 1.0) < UNCERTAIN_THRESHOLD

    def mark_doubt(self, entry_id: int):
        conn = self._store._get_conn()
        row = conn.execute(
            "SELECT type_data FROM long_term_entries WHERE id=? AND active=1",
            (entry_id,),
        ).fetchone()
        if row:
            td = json.loads(row["type_data"] or "{}")
            td["metamemory_doubt"] = True
            conn.execute(
                "UPDATE long_term_entries SET type_data=? WHERE id=?",
                (json.dumps(td, ensure_ascii=False), entry_id),
            )
            conn.commit()

    # ── Query Helpers ──────────────────────────────────────────

    def search_active(self, query: str, limit: int = 10,
                       memory_type: str = "",
                       user_id: str = "") -> List[dict]:
        conn = self._store._get_conn()
        conditions = ["active=1"]
        params = []
        if memory_type:
            conditions.append("memory_type=?")
            params.append(memory_type)
        if user_id:
            conditions.append("source_user_id=?")
            params.append(user_id)
        if query:
            conditions.append("(value LIKE ? OR key LIKE ?)")
            like_q = f"%{query}%"
            params.extend([like_q, like_q])

        sql = f"""SELECT id, memory_type, type_data, value, confidence, salience,
                   recall_strength, derivation, source_user_id, source_message_ts
                   FROM long_term_entries
                   WHERE {' AND '.join(conditions)}
                   ORDER BY salience * confidence DESC
                   LIMIT ?"""
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        results = []
        for r in rows:
            td = json.loads(r["type_data"] or "{}")
            results.append({
                "id": r["id"], "memory_type": r["memory_type"],
                "subcategory": td.get("subcategory", ""),
                "key": td.get("key", ""),
                "value": r["value"], "confidence": r["confidence"],
                "salience": r["salience"], "recall_strength": r["recall_strength"],
                "derivation": r["derivation"],
                "uncertain": r["recall_strength"] < UNCERTAIN_THRESHOLD,
                "source_user_id": r["source_user_id"],
            })
        return results

    def recompute_all_forgetting(self) -> dict:
        conn = self._store._get_conn()
        rows = conn.execute(
            """SELECT id, salience, reconsolidation_count, last_recalled_at,
               created_at, recall_strength
               FROM long_term_entries WHERE active=1"""
        ).fetchall()
        deleted = 0
        updated = 0
        for r in rows:
            new_strength = self.compute_recall_strength(
                r["salience"], r["reconsolidation_count"],
                r["last_recalled_at"], r["created_at"],
            )
            if new_strength < RECALL_DELETE_THRESHOLD:
                conn.execute(
                    "UPDATE long_term_entries SET active=0, deleted_at=? WHERE id=?",
                    (datetime.now(timezone.utc).isoformat(), r["id"]),
                )
                deleted += 1
            elif abs(new_strength - r["recall_strength"]) > 0.01:
                conn.execute(
                    "UPDATE long_term_entries SET recall_strength=? WHERE id=?",
                    (round(new_strength, 4), r["id"]),
                )
                updated += 1
        conn.commit()
        return {"deleted": deleted, "updated": updated}

    def build_prompt_block(self, max_chars: int = 2200) -> str:
        facts = self.get_high_confidence(0.4)
        if not facts:
            return ""
        blocks: Dict[str, List[str]] = {}
        for f in facts:
            blocks.setdefault(f.category, []).append(
                f"- [{f.key}] {f.value} (conf: {f.confidence:.1f})"
            )
        lines = []
        for cat, items in sorted(blocks.items()):
            lines.append(f"## {cat}")
            lines.extend(items[:8])
        return "\n".join(lines)[:max_chars]


def is_correction_message(text: str) -> bool:
    return bool(CN_CORRECTION_PATTERNS.search(text))
