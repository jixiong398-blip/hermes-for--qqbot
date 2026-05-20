"""
Workflow Memory (WFM) — procedural patterns with usage-based decay.

Key features:
  - Workflow records with trigger patterns, steps, preconditions
  - Usage-based weight decay (forgetting mechanism)
  - Natural language trigger matching
  - Success rate tracking
  - Auto-generation from repeated interaction patterns
  - Integration with Hermes skills system
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .store import MemoryStore, WorkflowEntry

logger = logging.getLogger(__name__)

# Decay constants
DECAY_HALF_LIFE_DAYS = 14.0  # Weight halves after 14 days without use
DECAY_MIN_WEIGHT = 0.01      # Minimum weight before a workflow is considered "forgotten"
DECAY_MAX_WEIGHT = 2.0       # Maximum weight cap
BOOST_PER_USE = 0.2          # Weight increase per successful use
PENALTY_PER_FAILURE = 0.1    # Weight decrease per failed use


def compute_decayed_weight(
    base_weight: float,
    last_used: float,
    usage_count: int,
    success_count: int,
    now: Optional[float] = None,
) -> float:
    """Compute current weight with time-based exponential decay and usage adjustment.

    Formula:
      time_decay = exp(-lambda * t)
      where lambda = ln(2) / half_life
      t = days since last use

      usage_bonus = min(1.0, log(1 + usage_count) / log(1 + 50)) * BOOST_PER_USE
      success_ratio = success_count / max(1, usage_count)
      adjusted = base_weight * (0.5 + 0.5 * success_ratio) * time_decay + usage_bonus

    The weight decays exponentially over time but gets a floor boost from
    accumulated usage history. Frequently used but rarely used workflows
    converge to near-zero while still retaining a small footprint.
    """
    if now is None:
        now = datetime.now(timezone.utc).timestamp()

    if last_used <= 0:
        # Never used — start decaying from base
        days_since = 30.0
    else:
        days_since = max(0.0, (now - last_used) / 86400.0)

    lambda_decay = math.log(2) / DECAY_HALF_LIFE_DAYS
    time_decay = math.exp(-lambda_decay * days_since)

    usage_bonus = min(1.0, math.log(1 + usage_count) / math.log(51)) * BOOST_PER_USE
    success_ratio = success_count / max(1, usage_count)
    adjusted = base_weight * (0.5 + 0.5 * success_ratio) * time_decay + usage_bonus

    return max(DECAY_MIN_WEIGHT, min(DECAY_MAX_WEIGHT, adjusted))


class WorkflowMemory:
    """Manages procedural workflow patterns with automatic forgetting."""

    def __init__(self, store: MemoryStore):
        self._store = store

    def add_workflow(self, name: str, description: str,
                     steps: List[str],
                     trigger_patterns: Optional[List[str]] = None,
                     preconditions: Optional[List[str]] = None,
                     expected_outcome: str = "",
                     session_id: Optional[str] = None,
                     base_weight: float = 1.0) -> int:
        """Create or update a workflow."""
        now = datetime.now(timezone.utc).timestamp()

        entry = WorkflowEntry(
            name=name,
            description=description,
            trigger_patterns=trigger_patterns or [],
            steps=steps,
            preconditions=preconditions or [],
            expected_outcome=expected_outcome,
            base_weight=base_weight,
            current_weight=base_weight,
            last_used=0.0,
            created_at=now,
            updated_at=now,
            source_session_ids=[session_id] if session_id else [],
        )
        return self._store.upsert_workflow(entry)

    def record_usage(self, workflow_name: str, success: bool = True):
        """Record a workflow being used, updating decay weight."""
        wf = self._store.get_workflow(workflow_name)
        if not wf:
            logger.warning("Recorded usage for unknown workflow: %s", workflow_name)
            return

        self._store.record_workflow_usage(wf.id, success)

        # Recompute current weight
        new_weight = compute_decayed_weight(
            wf.base_weight,
            datetime.now(timezone.utc).timestamp(),
            wf.usage_count + 1,
            wf.success_count + (1 if success else 0),
        )

        conn = self._store._get_conn()
        conn.execute(
            "UPDATE workflow_entries SET current_weight = ? WHERE id = ?",
            (new_weight, wf.id),
        )
        conn.commit()

    def apply_decay_all(self) -> List[Tuple[str, float, float]]:
        """Recompute weights for all workflows. Returns [(name, old_weight, new_weight), ...]."""
        now = datetime.now(timezone.utc).timestamp()
        workflows = self._store.get_all_workflows()
        changes = []

        conn = self._store._get_conn()
        for wf in workflows:
            new_weight = compute_decayed_weight(
                wf.base_weight, wf.last_used,
                wf.usage_count, wf.success_count, now,
            )
            if abs(new_weight - wf.current_weight) > 0.001:
                conn.execute(
                    "UPDATE workflow_entries SET current_weight = ?, updated_at = ? WHERE id = ?",
                    (new_weight, now, wf.id),
                )
                changes.append((wf.name, wf.current_weight, new_weight))

        conn.commit()
        return changes

    def get_relevant_workflows(self, query: str, max_weight: float = DECAY_MAX_WEIGHT) -> List[WorkflowEntry]:
        """Find workflows relevant to a query, weighted by current weight."""
        # First apply decay
        self.apply_decay_all()

        results = self._store.search_workflows(query, limit=10)
        # Filter and sort by current_weight
        results = [r for r in results if r.current_weight >= DECAY_MIN_WEIGHT]
        results.sort(key=lambda r: -r.current_weight)
        return results[:5]

    def match_trigger(self, user_message: str) -> List[WorkflowEntry]:
        """Find workflows whose trigger patterns match the user's message."""
        self.apply_decay_all()
        workflows = self._store.get_active_workflows(threshold=DECAY_MIN_WEIGHT)
        matched = []

        msg_lower = user_message.lower()
        for wf in workflows:
            score = 0.0
            for pattern in wf.trigger_patterns:
                pattern_lower = pattern.lower()
                if pattern_lower in msg_lower:
                    score += 1.0
                # Partial word matching
                pattern_words = set(pattern_lower.split())
                msg_words = set(msg_lower.split())
                overlap = len(pattern_words & msg_words)
                if overlap > 0:
                    score += overlap * 0.5 / max(len(pattern_words), 1)

            if score > 0:
                matched.append((wf, score * wf.current_weight))

        matched.sort(key=lambda x: -x[1])
        return [m[0] for m in matched[:3]]

    def get_forgotten_workflows(self) -> List[WorkflowEntry]:
        """Get workflows that have decayed below the minimum threshold."""
        self.apply_decay_all()
        return [w for w in self._store.get_all_workflows()
                if w.current_weight <= DECAY_MIN_WEIGHT * 2]

    def prune_forgotten(self, dry_run: bool = True) -> List[str]:
        """Remove workflows with weight below threshold.
        Returns list of removed workflow names."""
        self.apply_decay_all()
        all_wfs = self._store.get_all_workflows()
        to_remove = [w for w in all_wfs if w.current_weight <= DECAY_MIN_WEIGHT]

        if dry_run:
            return [w.name for w in to_remove]

        for wf in to_remove:
            self._store.delete_workflow(wf.id)
            logger.info("Pruned forgotten workflow: %s (weight=%.4f)", wf.name, wf.current_weight)

        return [w.name for w in to_remove]

    def build_prompt_block(self, active_only: bool = True) -> str:
        """Build a prompt block of currently active workflows."""
        self.apply_decay_all()
        if active_only:
            workflows = self._store.get_active_workflows(threshold=DECAY_MIN_WEIGHT)
        else:
            workflows = self._store.get_all_workflows()

        if not workflows:
            return ""

        lines = ["## Available Workflows (auto-detected procedural patterns)\n"]
        for wf in workflows[:10]:
            triggers = ", ".join(wf.trigger_patterns[:3]) if wf.trigger_patterns else "any"
            wf_line = (
                f"- **{wf.name}** (weight: {wf.current_weight:.2f}, "
                f"used: {wf.usage_count}x, success: {wf.success_count}/{wf.usage_count})\n"
                f"  Description: {wf.description[:150]}\n"
                f"  Triggers: {triggers}"
            )
            lines.append(wf_line)

        return "\n".join(lines)

    def suggest_workflow(self, session_topics: List[str],
                         session_patterns: List[str]) -> Optional[Dict]:
        """Suggest a new workflow based on repeated interaction patterns.
        Returns None if no clear pattern emerges."""
        if len(session_patterns) < 3:
            return None

        pattern_hash = hashlib.md5(
            "|".join(sorted(session_patterns)).encode()
        ).hexdigest()[:12]

        name = f"auto-wf-{pattern_hash}"
        suggested = {
            "name": name,
            "description": f"Auto-detected workflow for: {', '.join(session_topics[:3])}",
            "trigger_patterns": session_patterns[:5],
            "steps": session_patterns,
            "preconditions": [],
            "expected_outcome": "",
            "base_weight": 0.5,
            "topics": session_topics,
        }
        return suggested
