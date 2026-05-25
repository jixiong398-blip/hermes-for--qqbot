"""
Unified Memory Gateway — single entry point for all memory operations.

Coordinates all memory subsystems:
  - ShortTermMemory: conversation tracking per session
  - LongTermMemory: persistent facts/knowledge
  - WorkflowMemory: procedural patterns with decay
  - WikiKnowledgeBase: external knowledge integration
  - MemoryConsolidator: STM→LTM→WFM promotion
  - MemoryRetriever: multi-source recall with scoring

Usage:
  gateway = UnifiedMemoryGateway()
  
  # Per-turn processing
  gateway.process_turn(session_id, turn_index, "user", "How do I deploy?")
  
  # Recall at start of agent turn
  context = gateway.get_context_for_agent("How do I deploy?", session_id)
  
  # End-of-session consolidation (runs on session close/reset)
  gateway.consolidate(session_id)
  
  # Apply forgetting decay (call periodically)
  gateway.maintenance_cycle()
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .store import MemoryStore, _memory_db_path
from .short_term import ShortTermMemory
from .long_term import LongTermMemory
from .workflow import WorkflowMemory, DECAY_MIN_WEIGHT
from .wiki import WikiKnowledgeBase, KARPATHY_WIKI_REPO
from .retrieval import MemoryRetriever
from .consolidation import MemoryConsolidator

logger = logging.getLogger(__name__)


class UnifiedMemoryGateway:
    """Single entry point for the unified memory system.

    Provides:
      - Per-turn message processing with topic extraction
      - Multi-source recall for agent context enrichment
      - Automatic STM→LTM→WFM consolidation
      - Usage-based workflow decay (forgetting)
      - Wiki knowledge base auto-integration
      - System prompt block generation
      - Memory maintenance scheduling
    """

    _instances: Dict[str, "UnifiedMemoryGateway"] = {}
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls, name: str = "default") -> "UnifiedMemoryGateway":
        """Get or create a singleton instance (per name)."""
        with cls._lock:
            if name not in cls._instances:
                cls._instances[name] = cls(name=name)
            return cls._instances[name]

    def __init__(self, name: str = "default",
                 db_path: Optional[Path] = None,
                 wiki_dirs: Optional[List[Path]] = None,
                 enable_wiki: bool = True,
                 enable_workflow_decay: bool = True,
                 consolidation_min_turns: int = 6):
        self.name = name
        self._store = MemoryStore(db_path=db_path or _memory_db_path())
        self._stm = ShortTermMemory(self._store)
        self._ltm = LongTermMemory(self._store)
        self._wfm = WorkflowMemory(self._store)
        self._wiki = WikiKnowledgeBase(
            self._store,
            wiki_dirs=wiki_dirs or [],
            github_repos=[KARPATHY_WIKI_REPO] if enable_wiki else [],
        )
        self._retriever = MemoryRetriever(
            self._store, self._stm, self._ltm, self._wfm, self._wiki,
        )
        self._consolidator = MemoryConsolidator(
            self._store, self._stm, self._ltm, self._wfm,
        )
        self._consolidation_min_turns = consolidation_min_turns
        self._enable_workflow_decay = enable_workflow_decay
        self._enable_wiki = enable_wiki
        self._turn_counters: Dict[str, int] = {}
        self._last_maintenance: float = 0.0
        self._wiki_synced: bool = False
        self._obsidian_vault: Any = None

    # ── Per-Turn Processing ───────────────────────────────────

    def process_turn(self, session_id: str, role: str, content: str,
                     speaker_name: str = "",
                     chat_type: str = "dm",
                     bot_replied: bool = True,
                     topics: Optional[List[str]] = None,
                     intent: str = "",
                     emotional_tone: str = "") -> int:
        """Record a conversation turn in STM.

        Args:
            session_id: 会话ID
            role: user / assistant / other_user
            speaker_name: 发言者昵称 (群聊时必须)
            chat_type: dm 或 group
            bot_replied: 机器人是否回复了 (潜水=False)
        """
        turn_index = self._turn_counters.get(session_id, 0) + 1
        self._turn_counters[session_id] = turn_index

        if not topics and content:
            topics = self._stm.extract_topics_simple(content)

        entry_id = self._stm.add_turn(
            session_id=session_id,
            turn_index=turn_index,
            role=role,
            content=content,
            speaker_name=speaker_name,
            chat_type=chat_type,
            bot_replied=bot_replied,
            topics=topics,
            intent=intent,
            emotional_tone=emotional_tone,
        )
        return entry_id

    # ── Recall / Context Retrieval ────────────────────────────

    def recall(self, query: str, session_id: Optional[str] = None,
               max_chars: int = 4000) -> str:
        """Unified recall — returns formatted context for the LLM.

        This is the main method to call before an agent turn to
        inject relevant memories into the context.
        """
        # Auto-inject wiki context
        wiki_context = ""
        if self._enable_wiki:
            wiki_context = self._wiki.auto_context_injection(query)

        # Multi-source recall
        recall_prompt = self._retriever.build_recall_prompt(
            query, session_id, max_chars=max_chars,
        )

        # Match and inject relevant workflows
        wf_context = ""
        matched_wfs = self._wfm.match_trigger(query)
        if matched_wfs:
            wf_lines = ["## Matched Workflows\n"]
            for wf in matched_wfs[:3]:
                steps = " -> ".join(wf.steps[:5]) if wf.steps else "no steps defined"
                wf_lines.append(
                    f"- **{wf.name}** ({wf.current_weight:.2f}): {wf.description[:100]}\n"
                    f"  Steps: {steps}"
                )
            wf_context = "\n".join(wf_lines)

        # Obsidian vault search (auto-injected like wiki)
        obsidian_context = ""
        try:
            obsidian_context = self.get_obsidian_context(query, max_chars=1500)
        except Exception:
            pass

        parts = [p for p in [recall_prompt, wf_context, wiki_context, obsidian_context] if p]
        return "\n\n".join(parts)

    def get_context_for_agent(self, user_message: str,
                               session_id: Optional[str] = None,
                               chat_type: str = "dm") -> str:
        """Get the complete memory context to inject into the agent's prompt."""
        context = self.recall(user_message, session_id)
        # Prepend STM context with chat_type awareness
        if session_id:
            stm_context = self._stm.get_session_summary_context(session_id, chat_type)
            if stm_context:
                context = stm_context + "\n\n" + context
        return context

    def get_stm_context(self, session_id: str, chat_type: str = "dm") -> str:
        """Get short-term conversation context."""
        return self._stm.get_session_summary_context(session_id, chat_type)

    # ── Prompt Block Generation ───────────────────────────────

    def build_memory_prompt_block(self, max_chars: int = 3000) -> str:
        """Build a comprehensive memory block for the system prompt.

        Includes: LTM facts, active workflows. STM is per-turn,
        so it's injected separately via get_context_for_agent().
        """
        parts = []

        # Long-term memory block
        ltm_block = self._ltm.build_prompt_block(max_chars=max_chars // 2)
        if ltm_block:
            parts.append(ltm_block)

        # Active workflows block
        wf_block = self._wfm.build_prompt_block(active_only=True)
        if wf_block:
            parts.append(wf_block)

        return "\n\n".join(parts)

    # ── Consolidation (Session End) ───────────────────────────

    def consolidate(self, session_id: str) -> Dict[str, Any]:
        """Run the full consolidation pipeline at session end.

        This is the "sleep phase" that converts ephemeral session data
        into persistent structured memories.
        """
        stats = self._consolidator.consolidate(session_id)
        return stats

    def consolidate_if_needed(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Run consolidation only if enough turns have accumulated.

        Checks both the in-memory turn counter (for active sessions)
        and the actual STM database entries (for sessions recorded
        by other processes, e.g. the agent subprocess).
        """
        # Check in-memory counter for sessions still live in this process
        turn_count = self._turn_counters.get(session_id, 0)
        if turn_count >= self._consolidation_min_turns:
            return self.consolidate(session_id)

        # Check database entries — covers sessions where process_turn()
        # was called in the agent subprocess rather than the gateway
        try:
            entries = self.stm.get_recent(session_id, n=self._consolidation_min_turns)
            if len(entries) >= self._consolidation_min_turns:
                return self.consolidate(session_id)
        except Exception:
            pass

        return None

    # ── Maintenance & Forgetting ──────────────────────────────

    def maintenance_cycle(self) -> Dict[str, Any]:
        """Run a full maintenance cycle: decay, pruning, cleanup.

        Should be called periodically (e.g., every hour or on startup).
        """
        now = datetime.now(timezone.utc).timestamp()
        self._last_maintenance = now

        stats = {
            "workflow_decay": [],
            "workflows_pruned": [],
            "stm_pruned": 0,
        }

        # Apply workflow decay
        if self._enable_workflow_decay:
            decay_changes = self._wfm.apply_decay_all()
            stats["workflow_decay"] = [
                {"name": name, "old": old, "new": new}
                for name, old, new in decay_changes
            ]

            # Prune forgotten workflows
            pruned = self._wfm.prune_forgotten(dry_run=False)
            stats["workflows_pruned"] = pruned

        # Prune old STM entries
        self._store.prune_short_term(max_age_days=7.0)

        # Lightweight maintenance (FTS5 rebuild, pragma optimize)
        # Full VACUUM with freelist threshold is only run when needed
        self._store.quick_maintenance()

        return stats

    # ── Wiki Sync ─────────────────────────────────────────────

    def sync_wiki(self, force: bool = False) -> Dict[str, int]:
        """Synchronize the wiki knowledge base."""
        if not self._enable_wiki:
            return {"status": "wiki_disabled"}

        stats = self._wiki.sync(force=force)
        self._wiki_synced = True
        return stats

    def ensure_wiki_synced(self):
        """Sync wiki if not yet synced."""
        if self._enable_wiki and not self._wiki_synced:
            try:
                self.sync_wiki()
            except Exception as e:
                logger.warning("Wiki sync failed: %s", e)

    # ── Obsidian Vault ─────────────────────────────────────────

    @property
    def obsidian(self):
        """Lazy-load the Obsidian vault with auto-index on first access."""
        if self._obsidian_vault is None:
            from agent.memory.obsidian import ObsidianVault
            from pathlib import Path

            vault_path_str = os.environ.get("OBSIDIAN_VAULT_PATH", "")
            if vault_path_str:
                vault_path = Path(vault_path_str)
            else:
                vault_path = Path.home() / ".hermes" / "knowledge"

            try:
                self._obsidian_vault = ObsidianVault(vault_path)
            except Exception as e:
                import traceback
                logger.error("Obsidian vault creation failed: %s\n%s", e, traceback.format_exc())
                return None
            try:
                self._obsidian_vault.index()
            except Exception as e:
                import traceback
                logger.error("Obsidian vault index failed: %s\n%s", e, traceback.format_exc())
        return self._obsidian_vault

    def index_obsidian(self, force: bool = False) -> Dict[str, int]:
        """Index the Obsidian vault."""
        vault = self.obsidian
        if vault is None:
            return {"added": 0, "updated": 0, "skipped": 0}
        return vault.index(force=force)

    def search_obsidian(self, query: str, top_k: int = 5) -> List[Dict]:
        """Search the Obsidian vault."""
        vault = self.obsidian
        if vault is None:
            return []
        results = vault.search(query, top_k=top_k)
        return [
            {
                "title": note.title,
                "path": note.rel_path,
                "score": round(score, 3),
                "tags": note.tags[:10],
                "is_moc": note.is_moc,
                "snippet": note.snippet(800),
                "linked_notes": note.wikilinks[:5],
            }
            for note, score in results
        ]

    def get_obsidian_context(self, query: str, max_chars: int = 2000) -> str:
        """Get obsidian search results as prompt context."""
        vault = self.obsidian
        if vault is None:
            return ""
        return vault.build_search_context(query, max_chars=max_chars)

    def get_obsidian_stats(self) -> Dict:
        """Get obsidian vault statistics."""
        vault = self.obsidian
        if vault is None:
            return {
                "vault_path": "",
                "total_notes": 0,
                "total_links": 0,
                "total_backlinks": 0,
                "moc_notes": 0,
                "unique_tags": 0,
                "tags": [],
            }
        return vault.stats()

    # ── LTM Manual Operations ─────────────────────────────────

    def add_long_term(self, category: str, key: str, value: str,
                      tags: Optional[List[str]] = None,
                      confidence: float = 0.5) -> int:
        """Manually add a long-term memory fact."""
        return self._ltm.add_fact(category, key, value, tags, confidence)

    def search_long_term(self, query: str, limit: int = 10) -> List[Dict]:
        """Search long-term memory."""
        results = self._ltm.search(query, limit)
        return [
            {"id": r.id, "category": r.category, "key": r.key,
             "value": r.value, "confidence": r.confidence}
            for r in results
        ]

    def delete_long_term(self, entry_id: int):
        """Delete a long-term memory fact."""
        self._ltm.delete_fact(entry_id)

    # ── Workflow Manual Operations ────────────────────────────

    def add_workflow(self, name: str, description: str, steps: List[str],
                     trigger_patterns: Optional[List[str]] = None,
                     base_weight: float = 1.0) -> int:
        """Manually add a workflow."""
        return self._wfm.add_workflow(
            name=name, description=description, steps=steps,
            trigger_patterns=trigger_patterns, base_weight=base_weight,
        )

    def search_workflows(self, query: str) -> List[Dict]:
        """Search workflows."""
        wfs = self._wfm.get_relevant_workflows(query)
        return [
            {"name": w.name, "description": w.description,
             "weight": w.current_weight, "usage": w.usage_count,
             "success": w.success_count}
            for w in wfs
        ]

    def record_workflow_use(self, name: str, success: bool = True):
        """Record a workflow being used (manual trigger)."""
        self._wfm.record_usage(name, success)

    # ── Stats & Diagnostics ───────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive memory system statistics."""
        return {
            "store": self._store.get_store_stats(),
            "wiki": self._wiki.get_stats(),
            "active_sessions": len(self._turn_counters),
            "last_maintenance": self._last_maintenance,
            "workflow_decay_enabled": self._enable_workflow_decay,
            "wiki_enabled": self._enable_wiki,
        }

    def get_workflow_decay_report(self) -> List[Dict]:
        """Get a report of workflow weights for monitoring."""
        wfs = self._wfm._store.get_all_workflows()
        return [
            {
                "name": w.name,
                "current_weight": w.current_weight,
                "usage_count": w.usage_count,
                "success_rate": w.success_count / max(1, w.usage_count),
                "last_used_days_ago": (
                    (datetime.now(timezone.utc).timestamp() - w.last_used) / 86400.0
                    if w.last_used > 0 else float("inf")
                ),
                "status": (
                    "forgotten" if w.current_weight <= DECAY_MIN_WEIGHT
                    else "decaying" if w.current_weight < 0.3
                    else "active"
                ),
            }
            for w in wfs
        ]

    # ── Lifecycle ─────────────────────────────────────────────

    def on_session_start(self, session_id: str):
        """Called when a new session starts."""
        self._turn_counters[session_id] = 0
        self.ensure_wiki_synced()

    def on_session_end(self, session_id: str):
        """Called when a session ends. Triggers consolidation."""
        self.consolidate_if_needed(session_id)
        if session_id in self._turn_counters:
            del self._turn_counters[session_id]

    def shutdown(self):
        """Clean shutdown."""
        self._store.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.shutdown()
