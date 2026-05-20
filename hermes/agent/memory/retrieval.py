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
from .short_term import ShortTermMemory, ShortTermEntry
from .long_term import LongTermMemory, LongTermEntry
from .workflow import WorkflowMemory, WorkflowEntry
from .wiki import WikiKnowledgeBase, WikiEntry

logger = logging.getLogger(__name__)


# Source weights for retrieval scoring
DEFAULT_SOURCE_WEIGHTS = {
    "short_term": 1.0,
    "long_term": 0.8,
    "workflow": 0.6,
    "wiki": 0.4,
}

# Maximum context budget per retrieval (in characters)
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
                 source_weights: Optional[Dict[str, float]] = None,
                 context_budget: int = DEFAULT_CONTEXT_BUDGET):
        self._store = store
        self.stm = stm
        self.ltm = ltm
        self.wfm = wfm
        self.wiki = wiki
        self.source_weights = source_weights or DEFAULT_SOURCE_WEIGHTS.copy()
        self.context_budget = context_budget

    def recall(self, query: str, session_id: Optional[str] = None,
               include_sources: Optional[List[str]] = None,
               limit_per_source: int = 5) -> List[RetrievalResult]:
        """Unified recall across all memory sources.

        Args:
            query: The search query (user message or topic)
            session_id: Current session ID for STM context
            include_sources: Which sources to query (None = all)
            limit_per_source: Max results per source

        Returns:
            Ranked list of RetrievalResult objects
        """
        sources = include_sources or list(self.source_weights.keys())
        all_results: List[RetrievalResult] = []

        if "short_term" in sources and session_id:
            stm_results = self._retrieve_stm(query, session_id, limit_per_source)
            all_results.extend(stm_results)

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

    def _retrieve_stm(self, query: str, session_id: str, limit: int) -> List[RetrievalResult]:
        """Retrieve from short-term memory."""
        entries = self.stm.get_recent(session_id, n=limit * 2)
        results = []

        query_lower = query.lower()
        query_words = set(re.split(r'\W+', query_lower))

        for entry in entries:
            content_lower = entry.content.lower()
            content_words = set(re.split(r'\W+', content_lower))

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

    def _retrieve_ltm(self, query: str, limit: int) -> List[RetrievalResult]:
        """Retrieve from long-term memory."""
        entries = self.ltm.search(query, limit)
        results = []

        for entry in entries:
            relevance = entry.confidence * 0.7 + min(entry.retrieval_count / 50.0, 0.3)
            results.append(RetrievalResult(
                source="long_term",
                relevance=relevance,
                content=f"[{entry.category}] {entry.key}: {entry.value[:300]}",
                metadata={
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
            relevance = 0.5 + min(entry.retrieval_count / 20.0, 0.5)
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
            "long_term": [],
            "workflow": [],
            "wiki": [],
        }

        section_labels = {
            "short_term": "### Recent Context",
            "long_term": "### Relevant Knowledge",
            "workflow": "### Available Workflows",
            "wiki": "### Wiki Reference",
        }

        total_chars = 0
        for r in results:
            source_section = sections[r.source]
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

    def quick_recall(self, query: str, session_id: Optional[str] = None) -> List[RetrievalResult]:
        """Fast recall with tight limits for real-time use."""
        return self.recall(query, session_id, limit_per_source=3)
