"""
Memory Retriever — multi-source recall with relevance scoring.

Coordinates retrieval across all memory subsystems:
  - Short-term (recent turns, current session topics)
  - Long-term (persistent facts with confidence scores)
  - Workflow (procedural patterns with decay weights)
  - Wiki (external knowledge chunks)

Scoring:
  - Each source has a configurable weight
  - Results are deduplicated and ranked
  - Context window budget management
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Tuple

from .store import MemoryStore
from .short_term import ShortTermMemory, ShortTermEntry, tokenize_for_match
from .long_term import LongTermMemory, LongTermEntry
from .workflow import WorkflowMemory, WorkflowEntry
from .wiki import WikiKnowledgeBase, WikiEntry

logger = logging.getLogger(__name__)


DEFAULT_SOURCE_WEIGHTS = {
    "short_term": 1.0,
    "episode": 0.9,
    "long_term": 0.8,
    "workflow": 0.6,
    "wiki": 0.4,
}

SECTION_LABELS = {
    "short_term": "### Recent Context",
    "episode": "### 别处的印象 (其他会话, 非本群)",
    "long_term": "### Relevant Knowledge",
    "workflow": "### Available Workflows",
    "wiki": "### Wiki Reference",
}

DEFAULT_CONTEXT_BUDGET = 4000


class RetrievalResult:
    """Unified retrieval result from any memory source."""

    def __init__(self, source: str, relevance: float, content: str,
                 metadata: Optional[Dict] = None):
        self.source = source
        self.relevance = relevance
        self.content = content
        self.metadata = metadata or {}

    def __repr__(self):
        return f"RetrievalResult(source={self.source}, relevance={self.relevance:.3f})"


class MemoryRetriever:
    """Multi-source memory retrieval with relevance scoring and budget management."""

    def __init__(self, store: MemoryStore,
                 stm: ShortTermMemory,
                 ltm: LongTermMemory,
                 wfm: WorkflowMemory,
                 wiki: WikiKnowledgeBase,
                 epi=None,
                 source_weights: Optional[Dict[str, float]] = None,
                 context_budget: int = DEFAULT_CONTEXT_BUDGET):
        self._store = store
        self.stm = stm
        self.ltm = ltm
        self.wfm = wfm
        self.wiki = wiki
        self.epi = epi
        self.source_weights = source_weights or DEFAULT_SOURCE_WEIGHTS.copy()
        self.context_budget = context_budget

    def recall(self, query: str, session_id: Optional[str] = None,
               include_sources: Optional[List[str]] = None,
               limit_per_source: int = 5,
               chat_type: Optional[str] = None) -> List[RetrievalResult]:
        """Unified recall across all memory sources.

        Args:
            query: The search query (user message or topic)
            session_id: Current session ID for STM context
            chat_type: Optional explicit chat scope. ``None`` preserves
                legacy session-only retrieval; when provided it is passed to
                STM and EPI so a shared/legacy session cannot cross scopes.
            include_sources: Which sources to query (None = all)
            limit_per_source: Max results per source

        Returns:
            Ranked list of RetrievalResult objects
        """
        sources = include_sources or list(self.source_weights.keys())
        all_results: List[RetrievalResult] = []

        if "short_term" in sources and session_id:
            stm_results = self._retrieve_stm(
                query, session_id, limit_per_source, chat_type=chat_type
            )
            all_results.extend(stm_results)

        if "episode" in sources and self.epi is not None:
            all_results.extend(
                self._retrieve_episodes(
                    query, session_id, chat_type=chat_type
                )
            )

        if "long_term" in sources:
            ltm_results = self._retrieve_ltm(query, limit_per_source)
            all_results.extend(ltm_results)

        if "workflow" in sources:
            wfm_results = self._retrieve_wfm(query, limit_per_source)
            all_results.extend(wfm_results)

        if "wiki" in sources:
            wiki_results = self._retrieve_wiki(query, limit_per_source)
            all_results.extend(wiki_results)

        # Rank by relevance * source_weight
        all_results.sort(key=lambda r: -r.relevance * self.source_weights.get(r.source, 0.5))
        return all_results

    def _retrieve_stm(
        self,
        query: str,
        session_id: str,
        limit: int,
        *,
        chat_type: Optional[str] = None,
    ) -> List[RetrievalResult]:
        """Retrieve from short-term memory."""
        entries = self.stm.get_recent(
            session_id,
            n=limit * 2,
            chat_type=chat_type,
        )
        results = []

        query_words = set(tokenize_for_match(query))
        if not query_words:
            return []

        for entry in entries:
            content_words = set(tokenize_for_match(entry.content))

            # Relevance: word overlap + topic match
            word_overlap = len(query_words & content_words) / max(len(query_words), 1)
            topic_overlap = 0.0
            if entry.topics:
                topic_overlap = len(set(t.lower() for t in entry.topics) & query_words) / max(len(entry.topics), 1)

            relevance = 0.6 * word_overlap + 0.4 * topic_overlap

            if relevance > 0.05:
                results.append(RetrievalResult(
                    source="short_term",
                    relevance=relevance,
                    content=f"[Turn {entry.turn_index}] {entry.role}: {entry.content[:300]}",
                    metadata={
                        "turn_index": entry.turn_index,
                        "role": entry.role,
                        "topics": entry.topics,
                    },
                ))

        results.sort(key=lambda r: -r.relevance)
        return results[:limit]

    def _retrieve_episodes(
        self,
        query: str,
        session_id: Optional[str],
        *,
        chat_type: Optional[str] = None,
    ) -> List[RetrievalResult]:
        try:
            frags = self.epi.search(
                query,
                exclude_session=session_id,
                target_chat_type=chat_type,
            )
        except Exception:
            logger.debug("episode search failed", exc_info=True)
            return []

        import time as _time
        now = _time.time()
        results = []
        for f in frags:
            rendered = f.render(now)
            if not rendered:
                continue
            results.append(RetrievalResult(
                source="episode",
                relevance=f.score,
                content=rendered,
                metadata={
                    "id": f.id,
                    "scope": f.scope,
                    "share_level": f.share_level,
                    "age_days": round(f.age_days(now), 1),
                    "topics": f.topics,
                    "cross_session": True,
                },
            ))
        return results

    def _retrieve_ltm(self, query: str, limit: int) -> List[RetrievalResult]:
        """Retrieve from long-term memory."""
        entries = self.ltm.search(query, limit)
        results = []

        for entry in entries:
            # retrieval_count / 15 — calibrated against the 1-day recall_strength
            # half-life: ~15 recalls is what it takes to saturate the bonus,
            # matching how quickly an LTM fact decays when unrecalled.
            relevance = entry.confidence * 0.7 + min(entry.retrieval_count / 15.0, 0.3)
            results.append(RetrievalResult(
                source="long_term",
                relevance=relevance,
                content=f"[{entry.category}] {entry.key}: {entry.value[:300]}",
                metadata={
                    "id": entry.id,
                    "category": entry.category,
                    "key": entry.key,
                    "confidence": entry.confidence,
                    "retrieval_count": entry.retrieval_count,
                },
            ))

        return results

    def _retrieve_wfm(self, query: str, limit: int) -> List[RetrievalResult]:
        """Retrieve from workflow memory."""
        entries = self.wfm.get_relevant_workflows(query)
        results = []

        for entry in entries[:limit]:
            steps_preview = " \u2192 ".join(entry.steps[:3]) if entry.steps else "no steps"
            content = f"Workflow: {entry.name}\n{entry.description}\nSteps: {steps_preview}"

            results.append(RetrievalResult(
                source="workflow",
                relevance=entry.current_weight,
                content=content[:500],
                metadata={
                    "name": entry.name,
                    "weight": entry.current_weight,
                    "usage_count": entry.usage_count,
                    "success_count": entry.success_count,
                },
            ))

        return results

    def _retrieve_wiki(self, query: str, limit: int) -> List[RetrievalResult]:
        """Retrieve from wiki knowledge base."""
        entries = self.wiki.search(query, limit)
        results = []

        for entry in entries:
            relevance = 0.5 + min(entry.retrieval_count / 8.0, 0.5)
            results.append(RetrievalResult(
                source="wiki",
                relevance=relevance,
                content=f"[{entry.title}] {entry.content[:300]}",
                metadata={
                    "title": entry.title,
                    "section": entry.section,
                    "source_url": entry.source_url,
                },
            ))

        return results

    def build_recall_prompt(self, query: str, session_id: Optional[str] = None,
                            max_chars: Optional[int] = None) -> str:
        """Build a comprehensive recall prompt section for the LLM context.

        Orchestrates multi-source retrieval and formats results into a
        structured prompt block, respecting the context budget.
        """
        budget = max_chars or self.context_budget
        results = self.recall(query, session_id)

        if not results:
            return ""

        sections: Dict[str, List[str]] = {
            "short_term": [],
            "episode": [],
            "long_term": [],
            "workflow": [],
            "wiki": [],
        }

        section_labels = {
            "short_term": "### Recent Context",
            "episode": "### 别处的印象 (其他会话, 非本群)",
            "long_term": "### Relevant Knowledge",
            "workflow": "### Available Workflows",
            "wiki": "### Wiki Reference",
        }

        total_chars = 0
        for r in results:
            source_section = sections.setdefault(r.source, [])
            label = section_labels.get(r.source, f"### {r.source}")

            if not source_section:
                source_section.append(label)

            chunk = f"- {r.content}"
            if total_chars + len(chunk) > budget:
                break

            source_section.append(chunk)
            total_chars += len(chunk)

        prompt_parts = []
        for source, lines in sections.items():
            if len(lines) > 1:  # Has label + at least one result
                prompt_parts.append("\n".join(lines))

        return "\n\n".join(prompt_parts) if prompt_parts else ""

    def build_recall_prompt_from_results(self, results: List[RetrievalResult],
                                          query: str,
                                          max_chars: Optional[int] = None) -> str:
        """Build recall prompt from already-retrieved results (no re-query)."""
        budget = max_chars or self.context_budget
        if not results:
            return ""

        sections: Dict[str, List[str]] = {
            "short_term": [],
            "episode": [],
            "long_term": [],
            "workflow": [],
            "wiki": [],
        }
        section_labels = {
            "short_term": "### Recent Context",
            "episode": "### 别处的印象 (其他会话, 非本群)",
            "long_term": "### Relevant Knowledge",
            "workflow": "### Available Workflows",
            "wiki": "### Wiki Reference",
        }

        total_chars = 0
        for r in results:
            source_section = sections.get(r.source, [])
            if not source_section:
                source_section.append(section_labels.get(r.source, f"### {r.source}"))
            chunk = f"- {r.content}"
            if total_chars + len(chunk) > budget:
                break
            source_section.append(chunk)
            sections[r.source] = source_section
            total_chars += len(chunk)

        prompt_parts = []
        for source, lines in sections.items():
            if len(lines) > 1:
                prompt_parts.append("\n".join(lines))
        return "\n\n".join(prompt_parts) if prompt_parts else ""

    def quick_recall(self, query: str, session_id: Optional[str] = None) -> List[RetrievalResult]:
        """Fast recall with tight limits for real-time use."""
        return self.recall(query, session_id, limit_per_source=3)

    def graph_expand(self, seed_ids: List[int],
                     traversable_relations: Optional[List[str]] = None) -> List[RetrievalResult]:
        """1-hop graph walk expansion from BM25 seed memory IDs.

        Traverses edges (related_to, supports, abstracts_from) to find
        adjacent memories not in the seed set. Skips corrected_by/contradicts
        (those point to outdated/superseded memories).
        """
        if not seed_ids:
            return []

        if traversable_relations is None:
            traversable_relations = ["related_to", "supports", "abstracts_from"]

        conn = self._store._get_conn()
        placeholders = ",".join("?" * len(seed_ids))
        rel_placeholders = ",".join("?" * len(traversable_relations))

        rows = conn.execute(
            f"""SELECT DISTINCT me.dst_id, me.relation, me.weight,
                      le.id, le.category, le.key, le.value, le.confidence
               FROM memory_edges me
               JOIN long_term_entries le ON me.dst_id = le.id
               WHERE me.src_id IN ({placeholders})
               AND me.relation IN ({rel_placeholders})
               AND le.active = 1
               AND me.dst_id NOT IN ({placeholders})""",
            seed_ids + traversable_relations + seed_ids,
        ).fetchall()

        results = []
        seen = set(seed_ids)
        for row in rows:
            if row[3] in seen:
                continue
            seen.add(row[3])
            score = (row[2] or 0.5) * 0.6 + (row[6] or 0.5) * 0.4
            results.append(RetrievalResult(
                source="long_term",
                relevance=score,
                content=f"[{row[4]}] {row[5]}: {row[6][:300]}",
                metadata={
                    "id": row[3],
                    "category": row[4],
                    "key": row[5],
                    "confidence": row[6],
                    "edge_relation": row[1],
                    "edge_weight": row[2],
                    "graph_expanded": True,
                },
            ))
        results.sort(key=lambda r: -r.relevance)
        return results[:5]
