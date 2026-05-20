"""
Long-Term Memory (LTM) — persistent facts, user profile, knowledge.

Improvements over built-in MEMORY.md:
  - Categorized storage with confidence scoring
  - FTS5 full-text search
  - Auto-consolidation from STM patterns
  - Retrieval frequency tracking
  - Atomic updates with audit trail
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .store import MemoryStore, LongTermEntry

logger = logging.getLogger(__name__)

CATEGORIES = [
    "user_profile",      # Who the user is
    "user_preferences",  # User likes/dislikes
    "agent_identity",    # Agent's own notes about itself
    "knowledge",         # General facts/knowledge
    "decisions",         # Past decisions and their outcomes
    "relationships",     # People/entity relationships
    "coding",           # Code-related knowledge
    "general",          # Uncategorized
]


class LongTermMemory:
    """Manages persistent long-term facts and knowledge."""

    def __init__(self, store: MemoryStore):
        self._store = store

    def add_fact(self, category: str, key: str, value: str,
                 tags: Optional[List[str]] = None,
                 confidence: float = 0.5,
                 session_id: Optional[str] = None) -> int:
        """Add or update a fact in LTM."""
        if category not in CATEGORIES:
            logger.warning("Unknown LTM category '%s', using 'general'", category)
            category = "general"

        entry = LongTermEntry(
            category=category,
            key=key,
            value=value,
            tags=tags or [],
            confidence=min(1.0, max(0.0, confidence)),
            source_session_ids=[session_id] if session_id else [],
            created_at=datetime.now(timezone.utc).timestamp(),
            updated_at=datetime.now(timezone.utc).timestamp(),
        )
        return self._store.upsert_long_term(entry)

    def get_fact(self, category: str, key: str) -> Optional[LongTermEntry]:
        """Get a specific fact by category and key."""
        results = self._store.get_long_term(category=category, limit=1)
        for r in results:
            if r.key == key:
                return r
        return None

    def get_category(self, category: str, limit: int = 50) -> List[LongTermEntry]:
        """Get all facts in a category."""
        return self._store.get_long_term(category=category, limit=limit)

    def search(self, query: str, limit: int = 10) -> List[LongTermEntry]:
        """Full-text search across all facts."""
        results = self._store.search_long_term(query, limit)
        for r in results:
            self._store.record_ltm_retrieval(r.id)
        return results

    def get_all(self, limit: int = 100) -> List[LongTermEntry]:
        """Get all facts sorted by confidence."""
        return self._store.get_long_term(limit=limit)

    def get_high_confidence(self, min_confidence: float = 0.5) -> List[LongTermEntry]:
        """Get facts with confidence above threshold."""
        return self._store.get_ltm_by_confidence(min_confidence)

    def update_confidence(self, entry_id: int, delta: float):
        """Adjust confidence of a fact (positive or negative)."""
        conn = self._store._get_conn()
        row = conn.execute(
            "SELECT confidence FROM long_term_entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if row:
            new_conf = min(1.0, max(0.0, row["confidence"] + delta))
            conn.execute(
                "UPDATE long_term_entries SET confidence = ?, updated_at = ? WHERE id = ?",
                (new_conf, datetime.now(timezone.utc).timestamp(), entry_id),
            )
            conn.commit()

    def delete_fact(self, entry_id: int):
        """Remove a fact from LTM."""
        self._store.delete_long_term(entry_id)

    def build_prompt_block(self, max_chars: int = 2200) -> str:
        """Build a compact prompt block from high-confidence facts."""
        facts = self.get_high_confidence(0.4)
        if not facts:
            return ""

        blocks: Dict[str, List[str]] = {}
        for f in facts:
            blocks.setdefault(f.category, []).append(f"- {f.key}: {f.value[:200]}")

        parts = []
        total_chars = 0
        category_labels = {
            "user_profile": "## User Profile",
            "user_preferences": "## User Preferences",
            "agent_identity": "## Agent Notes",
            "knowledge": "## Knowledge",
            "decisions": "## Past Decisions",
            "relationships": "## Relationships",
            "coding": "## Code Knowledge",
            "general": "## General",
        }

        for cat in CATEGORIES:
            if cat in blocks and blocks[cat]:
                label = category_labels.get(cat, f"## {cat}")
                chunk = f"{label}\n" + "\n".join(blocks[cat])
                if total_chars + len(chunk) <= max_chars:
                    parts.append(chunk)
                    total_chars += len(chunk)

        return "\n\n".join(parts) if parts else ""

    def consolidate_from_stm(self, topics: List[str], session_id: str) -> int:
        """Check STM topics against existing LTM and reinforce matches.
        Returns the number of facts reinforced."""
        reinforced = 0
        for topic in topics:
            existing = self.search(topic, limit=3)
            if existing:
                for e in existing:
                    self.update_confidence(e.id, 0.05)
                    reinforced += 1
        return reinforced
