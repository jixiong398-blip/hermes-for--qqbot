"""
Memory Consolidator — STM → LTM → WFM promotion pipeline.

Implements the consolidation pipeline that runs at session boundaries:
  1. STM analysis: identify recurring topics, key facts, user intent patterns
  2. LTM promotion: new facts with sufficient evidence → LTM
  3. WFM detection: repeated action sequences → workflow candidates
  4. Confidence adjustment: reinforce/decay existing facts based on usage

This is the "sleep phase" of memory — converting ephemeral session data
into persistent structured knowledge.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .store import MemoryStore
from .short_term import ShortTermMemory
from .long_term import LongTermMemory
from .workflow import WorkflowMemory

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
            llm_summarizer: Optional async callable that takes text and returns summary

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
        }

        # Phase 1: Extract key information from STM
        topics, key_facts, patterns = self._extract_from_stm(entries)
        stats["topics_extracted"] = len(topics)

        # Phase 2: Promote to LTM
        facts_promoted, facts_reinforced = self._promote_to_ltm(
            topics, key_facts, session_id
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

        logger.info("Consolidation for session %s: promoted=%d, reinforced=%d, wf=%d",
                     session_id, facts_promoted, facts_reinforced, stats["workflows_suggested"])
        return stats

    def _extract_from_stm(self, entries: List) -> Tuple[List[str], Dict[str, List[str]], List[str]]:
        """Extract topics, key facts, and patterns from STM entries."""
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

        # Frequent topics
        frequent_topics = [
            t for t, c in topic_freq.items()
            if c >= CONSOLIDATION_MIN_TOPIC_FREQ
        ]

        # Extract key facts from user messages
        key_facts: Dict[str, List[str]] = {}
        for msg in all_user_msgs:
            facts = self._extract_simple_facts(msg)
            for fact_key, fact_value in facts:
                key_facts.setdefault(fact_key, []).append(fact_value)

        # Extract action patterns from assistant messages
        patterns = self._extract_action_patterns(all_assistant_msgs)

        return frequent_topics, key_facts, patterns

    # ── Chinese + English fact extraction patterns ────────────
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

    def _promote_to_ltm(self, topics: List[str],
                         key_facts: Dict[str, List[str]],
                         session_id: str) -> Tuple[int, int]:
        """Promote extracted facts to LTM, reinforcing existing ones.
        Logs each promotion/reinforcement to consolidation_log."""
        promoted = 0
        reinforced = 0

        # Promote key facts
        for fact_key, fact_values in key_facts.items():
            for value in fact_values[:3]:  # Top 3 values per key
                cat = self._categorize_fact(fact_key)
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
                    cat = self._categorize_fact(fact_key)
                    entry_id = self.ltm.add_fact(
                        category=cat,
                        key=fact_key,
                        value=value,
                        tags=[fact_key],
                        confidence=CONSOLIDATION_NEW_FACT_CONFIDENCE,
                        session_id=session_id,
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
                    # Layer 0 event stream
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
