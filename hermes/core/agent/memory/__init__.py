"""
Unified Memory Gateway — package init.

Usage:
  from agent.memory import UnifiedMemoryGateway
  
  gateway = UnifiedMemoryGateway.get_instance()
  gateway.process_turn(session_id, "user", "message")
  context = gateway.get_context_for_agent("query", session_id)
  gateway.consolidate(session_id)  # at session end
  gateway.maintenance_cycle()       # periodic cleanup

For skill auto-generation:
  from agent.memory import SkillAutoGenerator
  
  gen = SkillAutoGenerator(gateway)
  skills = gen.scan_and_generate()
"""

from .store import (
    MemoryStore,
    ShortTermEntry,
    LongTermEntry,
    WorkflowEntry,
    WikiEntry,
)
from .short_term import ShortTermMemory
from .long_term import LongTermMemory
from .workflow import (
    WorkflowMemory,
    compute_decayed_weight,
    DECAY_HALF_LIFE_DAYS,
    DECAY_MIN_WEIGHT,
    DECAY_MAX_WEIGHT,
)
from .wiki import WikiKnowledgeBase
from .retrieval import MemoryRetriever, RetrievalResult
from .consolidation import MemoryConsolidator
from .gateway import UnifiedMemoryGateway
from .skill_gen import SkillAutoGenerator

__all__ = [
    "MemoryStore",
    "ShortTermEntry",
    "LongTermEntry",
    "WorkflowEntry",
    "WikiEntry",
    "ShortTermMemory",
    "LongTermMemory",
    "WorkflowMemory",
    "WikiKnowledgeBase",
    "MemoryRetriever",
    "RetrievalResult",
    "MemoryConsolidator",
    "UnifiedMemoryGateway",
    "SkillAutoGenerator",
    "compute_decayed_weight",
    "DECAY_HALF_LIFE_DAYS",
    "DECAY_MIN_WEIGHT",
    "DECAY_MAX_WEIGHT",
]
