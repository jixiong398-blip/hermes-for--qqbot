"""
Memory Consolidator — STM → LTM → WFM promotion pipeline.

Implements the consolidation pipeline that runs at session boundaries:
  1. STM analysis: identify recurring topics, key facts, user intent patterns
  2. LTM promotion: new facts with sufficient evidence → LTM
  3. WFM detection: repeated action sequences → workflow candidates
  4. Confidence adjustment: reinforce/decay existing facts based on usage

This is the "sleep phase" of memory — converting ephemeral session data
into persistent structured knowledge.

Fact extraction uses DeepSeek API (driven by DEEPSEEK_API_KEY /
DEEPSEEK_BASE_URL / DEEPSEEK_MODEL env vars, same scheme as the OneBot
semantic_judge) to pull semantic facts out of natural conversation
that pure regex misses (emoji banter, sarcasm, implied preferences,
etc.). Falls back to the rule-based regex extractor if the API is
unconfigured or the request fails — consolidation is best-effort and
must never block on a network call.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .store import MemoryStore
from .short_term import ShortTermMemory
from .long_term import LongTermMemory
from .workflow import WorkflowMemory
from .date_resolver import resolve_relative_dates

# Layer 0 event stream — always-on, no external deps
try:
    from .event_stream import write_fact as _write_fact_event
    from .event_stream import write_decision as _write_decision_event
except ImportError:
    _write_fact_event = None  # type: ignore
    _write_decision_event = None  # type: ignore

logger = logging.getLogger(__name__)

CONSOLIDATION_MIN_TURNS = 6
CONSOLIDATION_MIN_TOPIC_FREQ = 2  # How many times a topic must appear to be consolidated
CONSOLIDATION_NEW_FACT_CONFIDENCE = 0.4
CONSOLIDATION_EXISTING_BOOST = 0.08

# LLM-based extraction timeout. The judge is best-effort — if it hangs,
# fall back to regex so consolidation still completes.
_LLM_EXTRACT_TIMEOUT = 30.0

# Capped STM payload sent to the LLM (characters per message + total chars).
# Keeps the prompt cheap; DeepSeek v4-flash handles ~16k easily but we
# don't need to dump everything — recent context is what matters.
_LLM_MSG_PREVIEW = 300
_LLM_TOTAL_BUDGET = 8000

# System prompt instructs the LLM to output strict JSON. We deliberately
# enumerate the 8 LTM categories so the model picks from the closed set
# rather than inventing its own. Salience hints (高考/结婚/生病=0.8+,
# preferences/tools=0.5, chitchat=0.2) mirror the regex heuristic in
# `_compute_salience` so the two paths stay calibrated.
_LLM_SYSTEM_PROMPT = """你是记忆蒸馏器。从对话里抽出值得长期记住的 fact。

只抽符合以下条件的：
- 用户身份、偏好、工具、活动、人际关系、决定、自我认知
- 用户明确表达的事实（"我叫 X"、"我喜欢 Y"、"我在做 Z"）
- 隐含但确凿的事实（多次提到同一工具=常用；多次求救同一主题=痛点）
- agent 自己应该记住的事（用户的情绪倾向、沟通习惯）

不要抽：
- 闲聊废话（"哈哈"、"嗯"、"好的"）
- 单次出现没后续的事实（瞬时情绪、临时吐槽）
- agent 自己的回复内容（只抽用户说的）

输出 JSON，fact 字段说明：
- category: 必须是这 8 个之一 user_profile / user_preferences / agent_identity / knowledge / decisions / relationships / coding / general
- key: 简短英文或拼音 key (≤30 字符)，snake_case
- value: 中文陈述句 (≤200 字符)，第三人称客观描述
- salience: 0.0-1.0，高考/结婚/生病/分手=0.8+；偏好/工具/活动=0.5；其它=0.3

patterns 字段：用户连续多次做的动作序列（如果有），中文短语描述。

只输出 JSON，不要多余文字：
{"facts": [{"category":"...", "key":"...", "value":"...", "salience":0.5}], "patterns": ["..."]}"""


def _llm_configured() -> bool:
    """True iff all three DeepSeek env vars are set. Used to short-circuit."""
    return bool(os.getenv("DEEPSEEK_API_KEY") and
                os.getenv("DEEPSEEK_BASE_URL") and
                os.getenv("DEEPSEEK_MODEL"))


def _call_llm_extract(messages: List[Dict[str, str]]) -> Optional[Dict[str, Any]]:
    """Call DeepSeek for fact extraction. Returns parsed JSON dict or None on failure.

    All API config comes from env vars — no hardcoded key / model / URL.
    Failure (no key, timeout, parse error, non-200) returns None so the
    caller can fall back to regex extraction.
    """
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    api_base = os.getenv("DEEPSEEK_BASE_URL", "")
    api_model = os.getenv("DEEPSEEK_MODEL", "")
    if not (api_key and api_base and api_model):
        return None

    # Build user prompt: compact transcript of recent turns
    lines: List[str] = []
    total = 0
    for m in messages:
        role = m.get("role", "")
        content = (m.get("content", "") or "")[:_LLM_MSG_PREVIEW]
        if not content:
            continue
        line = f"[{role}] {content}"
        if total + len(line) > _LLM_TOTAL_BUDGET:
            break
        lines.append(line)
        total += len(line)
    if not lines:
        return None
    user_prompt = "\n".join(lines)

    try:
        # Local import — keeps consolidation.py importable without requests
        # in environments that use httpx-only setups.
        import requests as _r
        resp = _r.post(
            f"{api_base.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": api_model,
                "messages": [
                    {"role": "system", "content": _LLM_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            },
            timeout=_LLM_EXTRACT_TIMEOUT,
        )
        if resp.status_code != 200:
            logger.debug("[Consolidator] LLM HTTP %s: %s",
                         resp.status_code, resp.text[:200])
            return None
        data = resp.json()
        msg = data["choices"][0]["message"]
        content = (msg.get("content") or "").strip()
        if not content:
            # Some models put the JSON inside reasoning_content when
            # response_format isn't honored. Try to recover it.
            reasoning = msg.get("reasoning_content") or ""
            json_match = re.search(r'\{[\s\S]*\}', reasoning)
            if json_match:
                content = json_match.group()
        if not content:
            return None
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            return None
        parsed.setdefault("facts", [])
        parsed.setdefault("patterns", [])
        return parsed
    except Exception as e:
        logger.debug("[Consolidator] LLM extract failed: %s", e)
        return None


class MemoryConsolidator:
    """Handles STM → LTM → WFM consolidation at session boundaries."""

    def __init__(self, store: MemoryStore,
                 stm: ShortTermMemory,
                 ltm: LongTermMemory,
                 wfm: WorkflowMemory):
        self._store = store
        self.stm = stm
        self.ltm = ltm
        self.wfm = wfm

    def consolidate(self, session_id: str,
                    llm_summarizer: Optional[Callable] = None) -> Dict[str, Any]:
        """Run the full consolidation pipeline for a session.

        Args:
            session_id: The session to consolidate
            llm_summarizer: Kept for backward-compat. Fact extraction
                now uses the DeepSeek API via _call_llm_extract().

        Returns:
            Dict with consolidation statistics
        """
        entries = self.stm.get_recent(session_id, n=100)
        if len(entries) < CONSOLIDATION_MIN_TURNS:
            return {"status": "skipped", "reason": f"too few turns ({len(entries)})"}

        stats = {
            "status": "completed",
            "stm_entries": len(entries),
            "topics_extracted": 0,
            "facts_promoted": 0,
            "facts_reinforced": 0,
            "workflows_suggested": 0,
            "session_id": session_id,
            "extraction_method": "regex",
        }

        # Phase 1: Extract key information from STM
        topics, structured_facts, patterns, extraction_method = self._extract_from_stm(entries)
        stats["topics_extracted"] = len(topics)
        stats["extraction_method"] = extraction_method

        reference_ts = entries[-1].created_at if entries else datetime.now(timezone.utc).timestamp()

        # Phase 2: Promote to LTM
        facts_promoted, facts_reinforced = self._promote_to_ltm(
            topics, structured_facts, session_id, reference_ts
        )
        stats["facts_promoted"] = facts_promoted
        stats["facts_reinforced"] = facts_reinforced

        # Phase 3: Detect workflow patterns
        if patterns:
            wf_suggested = self._detect_workflows(patterns, session_id)
            stats["workflows_suggested"] = wf_suggested

        # Phase 4: Apply decay to all workflows
        decay_changes = self.wfm.apply_decay_all()
        stats["workflows_decayed"] = len(decay_changes)

        # Phase 5: Mark STM entries as summarized
        if entries:
            max_turn = max(e.turn_index for e in entries)
            self._store.mark_summarized(session_id, max_turn)

        logger.info("Consolidation for session %s (method=%s): promoted=%d, reinforced=%d, wf=%d",
                     session_id, stats["extraction_method"],
                     facts_promoted, facts_reinforced, stats["workflows_suggested"])
        return stats

    def _extract_from_stm(self, entries: List) -> Tuple[List[str], List[Dict], List[str], str]:
        """Extract topics, structured facts, and patterns from STM entries.

        Returns (topics, structured_facts, patterns, method) where
        structured_facts is a list of dicts with keys: key, value,
        category, salience. The LLM path populates category/salience
        from the model output; the regex path falls back to
        _categorize_fact / _compute_salience.
        """
        from .store import ShortTermEntry

        topic_freq: Dict[str, int] = {}
        all_user_msgs: List[str] = []
        all_assistant_msgs: List[str] = []

        for e in entries:
            if not isinstance(e, ShortTermEntry):
                continue
            for topic in e.topics:
                topic_freq[topic] = topic_freq.get(topic, 0) + 1
            if e.role == "user":
                all_user_msgs.append(e.content)
            else:
                all_assistant_msgs.append(e.content)

        frequent_topics = [
            t for t, c in topic_freq.items()
            if c >= CONSOLIDATION_MIN_TOPIC_FREQ
        ]

        if _llm_configured():
            llm_messages = (
                [{"role": "user", "content": m} for m in all_user_msgs]
                + [{"role": "assistant", "content": m} for m in all_assistant_msgs]
            )
            llm_result = _call_llm_extract(llm_messages)
            if llm_result is not None:
                structured_facts: List[Dict] = []
                for fact in (llm_result.get("facts") or [])[:20]:
                    if not isinstance(fact, dict):
                        continue
                    key = (fact.get("key") or "").strip()[:30]
                    value = (fact.get("value") or "").strip()[:200]
                    if not key or not value:
                        continue
                    raw_cat = (fact.get("category") or "").strip()
                    raw_sal = fact.get("salience")
                    structured_facts.append({
                        "key": key,
                        "value": value,
                        "category": raw_cat if raw_cat in self._VALID_CATEGORIES else "general",
                        "salience": max(0.0, min(1.0, float(raw_sal))) if isinstance(raw_sal, (int, float)) else 0.3,
                    })
                patterns = list(llm_result.get("patterns") or [])[:10]
                return frequent_topics, structured_facts, patterns, "llm"

        structured_facts = []
        for msg in all_user_msgs:
            facts = self._extract_simple_facts(msg)
            for fact_key, fact_value in facts:
                cat = self._categorize_fact(fact_key)
                sal = self._compute_salience(fact_value, cat)
                structured_facts.append({
                    "key": fact_key[:30],
                    "value": fact_value[:200],
                    "category": cat,
                    "salience": sal,
                })

        patterns = self._extract_action_patterns(all_assistant_msgs)

        return frequent_topics, structured_facts, patterns, "regex"

    # ── Chinese + English fact extraction patterns (regex fallback) ───
    CN_FACT_PATTERNS = [
        # "我是/我叫 X" → identity
        (r'(?:我是|我叫|我是一名|我是个|我是做)\s*([^，。！？\n]{2,60})', "user_identity"),
        # "我用/我使用/我用的是 X" → tool/preference
        (r'(?:我用|我使用|我用的是|我日常用|我习惯用)\s*([^，。！？\n]{2,60})', "user_tool"),
        # "我喜欢/我爱好/我热爱 X" → preference
        (r'(?:我喜欢|我爱好|我热爱|我喜好|我偏爱|我偏好)\s*([^，。！？\n]{2,60})', "user_preference"),
        # "我讨厌/我不喜欢/我烦 X" → dislike
        (r'(?:我讨厌|我不喜欢|我烦|我恨|我受不了)\s*([^，。！？\n]{2,60})', "user_dislike"),
        # "我在做/我写/我做/我在写/我在搞 X" → activity
        (r'(?:我在做|我写|我做|我在写|我在搞|我在开发|我在维护|我在修)\s*([^，。！？\n]{2,60})', "current_activity"),
        # "我的 X 是 Y" → attribute
        (r'我的\s*([\u4e00-\u9fff\w]{1,20})\s*(?:是|叫|在)\s*([^，。！？\n]{2,60})', "user_attr"),
        # "我觉得/我认为/我想/我感觉 X" → opinion
        (r'(?:我觉得|我认为|我想|我感觉|我以为|我估计|我猜)\s*([^，。！？\n]{3,80})', "user_opinion"),
        # "X 需要/应该/必须/得 Y" → decision
        (r'([\u4e00-\u9fff\w]{1,30})\s*(?:需要|应该|必须|得|要|应当)\s*([^，。！？\n]{3,60})', "decision"),
        # "X 最好 Y" / "X 不能 Y" → constraint
        (r'([\u4e00-\u9fff\w]{1,30})\s*(?:最好|不能|不要|别)\s*([^，。！？\n]{3,60})', "constraint"),
    ]

    EN_FACT_PATTERNS = [
        # "I am/use/have/like/prefer/want X"
        (r'(?:I\s+(?:am|use|have|like|prefer|want|need|work with|develop|code in)\s+)([^.!?\n]{5,100})', "user_identity"),
        # "my X is Y"
        (r'my\s+(\w+(?:\s+\w+)?)\s+(?:is|are|was)\s+([^.!?\n]{5,100})', "user_attr"),
        # "X should/needs to/must Y"
        (r'(\w+(?:\s+\w+){0,3})\s+(?:should|needs?\s+to|must|has\s+to)\s+([^.!?\n]{5,100})', "decision"),
    ]

    def _extract_simple_facts(self, message: str) -> List[Tuple[str, str]]:
        """Extract facts from both Chinese and English text.

        Chinese patterns:
          "我是/我用/我喜欢/我写/我的X是Y/我觉得/X需要Y"
        English patterns:
          "I am/use/like/my X is Y/X should Y"
        """
        facts = []

        # ── Chinese patterns ──
        for pattern, category in self.CN_FACT_PATTERNS:
            if category in ("user_attr", "decision", "constraint"):
                # Two-group patterns → (key, value)
                matches = re.findall(pattern, message)
                for grp1, grp2 in matches[:3]:
                    facts.append((category, f"{grp1.strip()} → {grp2.strip()}"))
            else:
                # Single-group patterns → category is the key
                matches = re.findall(pattern, message)
                for val in matches[:3]:
                    facts.append((category, val.strip()))

        # ── English patterns ──
        for pattern, category in self.EN_FACT_PATTERNS:
            matches = re.findall(pattern, message, re.IGNORECASE)
            if category in ("user_attr", "decision"):
                for grp1, grp2 in matches[:3]:
                    facts.append((category, f"{grp1.strip()} → {grp2.strip()}"))
            else:
                for val in matches[:3]:
                    facts.append((category, val.strip()))

        return facts

    def _extract_action_patterns(self, messages: List[str]) -> List[str]:
        """Extract repeated action patterns from assistant messages."""
        if not messages:
            return []

        # Look for common action sequences
        action_indicators = [
            r'(?:step\s+\d+|first|then|next|finally|after\s+that)',
            r'(?:run|execute|create|write|edit|delete|install|build|test|deploy)',
            r'(?:let\s+me|I\'ll|we\'ll|you\s+should)',
        ]

        patterns = []
        for msg in messages:
            for indicator in action_indicators:
                matches = re.findall(indicator + r'[^.!?\n]{10,200}', msg, re.IGNORECASE)
                patterns.extend(matches[:3])

        return patterns[:10]

    _VALID_CATEGORIES = {
        "user_profile", "user_preferences", "agent_identity",
        "knowledge", "decisions", "relationships", "coding", "general",
    }

    def _promote_to_ltm(self, topics: List[str],
                         structured_facts: List[Dict],
                         session_id: str,
                         reference_ts: float = 0.0) -> Tuple[int, int]:
        """Promote extracted facts to LTM, reinforcing existing ones.
        Logs each promotion/reinforcement to consolidation_log.

        structured_facts items carry category/salience from the extractor
        (LLM or regex) — this method does NOT re-classify or re-score.
        """
        promoted = 0
        reinforced = 0

        seen_keys: Set[str] = set()
        for fact in structured_facts:
            fact_key = fact["key"]
            value = fact["value"]
            cat = fact["category"]
            sal = fact["salience"]
            if fact_key in seen_keys:
                continue
            seen_keys.add(fact_key)

            if reference_ts:
                value, _ = resolve_relative_dates(value, reference_ts)
            existing = self.ltm.get_fact(cat, fact_key)

            if existing:
                self.ltm.update_confidence(existing.id, CONSOLIDATION_EXISTING_BOOST)
                self._store.log_consolidation(
                    session_id=session_id,
                    source_type="short_term",
                    target_type="long_term",
                    source_id=0,
                    target_id=existing.id,
                    action="reinforce",
                    detail=f"{fact_key}: {value[:120]}",
                )
                reinforced += 1
            else:
                entry_id = self.ltm.add_fact(
                    category=cat,
                    key=fact_key,
                    value=value,
                    tags=[fact_key],
                    confidence=CONSOLIDATION_NEW_FACT_CONFIDENCE,
                    session_id=session_id,
                    salience=sal,
                )
                self._store.log_consolidation(
                    session_id=session_id,
                    source_type="short_term",
                    target_type="long_term",
                    source_id=0,
                    target_id=entry_id,
                    action="promote",
                    detail=f"{fact_key} [{cat}]: {value[:120]}",
                )
                if _write_fact_event:
                    try:
                        _write_fact_event(
                            category=cat,
                            key=fact_key,
                            value=value,
                            confidence=CONSOLIDATION_NEW_FACT_CONFIDENCE,
                            session_id=session_id,
                        )
                    except Exception:
                        pass
                promoted += 1

        # Promote frequent topics as knowledge facts
        for topic in topics[:5]:
            existing = self.ltm.get_fact("knowledge", f"topic_{topic}")
            if existing:
                self.ltm.update_confidence(existing.id, CONSOLIDATION_EXISTING_BOOST * 0.5)
                self._store.log_consolidation(
                    session_id=session_id,
                    source_type="short_term",
                    target_type="long_term",
                    source_id=0,
                    target_id=existing.id,
                    action="reinforce_topic",
                    detail=f"topic: {topic}",
                )
                reinforced += 1
            else:
                entry_id = self.ltm.add_fact(
                    category="knowledge",
                    key=f"topic_{topic}",
                    value=f"User discussed: {topic}",
                    tags=[topic],
                    confidence=CONSOLIDATION_NEW_FACT_CONFIDENCE * 0.5,
                    session_id=session_id,
                )
                self._store.log_consolidation(
                    session_id=session_id,
                    source_type="short_term",
                    target_type="long_term",
                    source_id=0,
                    target_id=entry_id,
                    action="promote_topic",
                    detail=f"topic: {topic}",
                )
                # Layer 0 event stream
                if _write_fact_event:
                    try:
                        _write_fact_event(
                            category="knowledge",
                            key=f"topic_{topic}",
                            value=f"User discussed: {topic}",
                            confidence=CONSOLIDATION_NEW_FACT_CONFIDENCE * 0.5,
                            session_id=session_id,
                        )
                    except Exception:
                        pass
                promoted += 1

        return promoted, reinforced

    def _categorize_fact(self, key: str) -> str:
        """Categorize a fact key into an LTM category."""
        if key.startswith("user_"):
            return "user_profile"
        if key in ("user_identity", "preference", "like", "dislike"):
            return "user_preferences"
        if key in ("decision",):
            return "decisions"
        if any(kw in key for kw in ("code", "programming", "language", "framework", "library")):
            return "coding"
        return "general"

    _SALIENCE_HIGH_CUES = ["高考", "结婚", "生日", "分手", "去世", "生病", "考试",
                           "搬家", "离职", "入职", "毕业", "旅行", "手术", "怀孕"]
    _SALIENCE_EMOTION_CUES = ["紧张", "开心", "难过", "害怕", "生气", "崩溃",
                              "激动", "失望", "感动", "心疼", "焦虑", "兴奋"]

    def _compute_salience(self, value: str, category: str) -> float:
        if not value:
            return 0.3
        s = 0.3
        if any(cue in value for cue in self._SALIENCE_HIGH_CUES):
            s = max(s, 0.8)
        if any(cue in value for cue in self._SALIENCE_EMOTION_CUES):
            s += 0.1
        if category in ("user_profile", "user_preferences"):
            s = max(s, 0.5)
        if category == "decisions":
            s = max(s, 0.6)
        return min(1.0, s)

    def _detect_workflows(self, patterns: List[str], session_id: str) -> int:
        """Detect potential workflows from action patterns."""
        if len(patterns) < 3:
            return 0

        # Look for repeated action sequences
        normalized = [p.lower().strip() for p in patterns]
        unique_patterns = list(dict.fromkeys(normalized))

        if len(unique_patterns) >= 2:
            name_hash = hashlib.md5(
                "|".join(sorted(unique_patterns)).encode()
            ).hexdigest()[:8]

            trigger_words = set()
            for p in unique_patterns:
                words = re.findall(r'\b[a-z]{4,}\b', p)
                trigger_words.update(words[:3])

            self.wfm.add_workflow(
                name=f"auto-session-{name_hash}",
                description=f"Detected from session {session_id}",
                steps=unique_patterns[:5],
                trigger_patterns=list(trigger_words)[:5],
                preconditions=[],
                session_id=session_id,
                base_weight=0.3,
            )
            return 1
        return 0
