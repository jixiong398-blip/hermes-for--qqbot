"""
Unified Memory Gateway Tool — LLM-facing interface for the memory system.

Exposes the UnifiedMemoryGateway to the agent via a single `memory_gateway` tool.
This supersedes the old `memory` tool with unified recall, workflow management,
wiki search, and skill auto-generation.

Actions:
  - recall: Search all memory sources (STM, LTM, WFM, Wiki) for relevant context
  - remember: Save a fact to long-term memory
  - correct: Correct a previously stored memory (supersede, not overwrite)
  - forget: Remove a fact from long-term memory
  - doubt: Mark a memory as uncertain
  - link: Create a relationship edge between two memories
  - search: Explicit semantic search (non-auto recall)
  - list_facts: List facts by category
  - list_workflows: List active workflows with weights
  - use_workflow: Record workflow usage (triggers weight boost)
  - suggest_skill: Suggest auto-generating a skill from workflows
  - wiki_search: Search the Karpathy Wiki knowledge base
  - stats: Get memory system statistics
  - consolidate: Manually trigger STM→LTM consolidation
  - self_audit: Scan for internal contradictions (v1: log only)
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

GATEWAY_SCHEMA = {
    "name": "memory_gateway",
    "description": (
        "Unified memory system with local Obsidian knowledge base (AI knowledge: Agent/LLM/Transformer/RAG/Prompt/Memory; "
        "Creative works: MyGO fan fiction 14.5万字, 落日余烬 6.9万字). "
        "Recall context, manage facts/workflows, search wiki and Obsidian knowledge base.\n"
        "Actions: recall, remember, forget, list_facts, list_workflows, use_workflow, suggest_skill, "
        "wiki_search, obsidian_search, obsidian_read, stats, consolidate, decay_report, timeline, "
        "core_remember (write your own core memory, first person), core_forget (soft delete by id), "
        "core_list (list your core memories)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "recall", "remember", "correct", "forget", "doubt", "link",
                    "search", "list_facts",
                    "list_workflows", "use_workflow", "suggest_skill",
                    "wiki_search", "obsidian_search", "obsidian_read",
                    "stats", "consolidate", "decay_report",
                    "timeline", "self_audit",
                    "core_remember", "core_forget", "core_list",
                ],
                "description": "The memory action to perform.",
            },
            "query": {
                "type": "string",
                "description": "Search query for recall, forget, wiki_search, obsidian_search, or note title for obsidian_read.",
            },
            "category": {
                "type": "string",
                "description": "LTM category: user_profile, user_preferences, agent_identity, knowledge, decisions, relationships, coding, general.",
            },
            "key": {"type": "string", "description": "Fact key for remember action."},
            "value": {"type": "string", "description": "Fact value for remember action."},
            "tags": {
                "type": "array", "items": {"type": "string"},
                "description": "Tags for the fact.",
            },
            "confidence": {
                "type": "number", "description": "Confidence 0.0-1.0.",
            },
            "fact_id": {"type": "integer", "description": "Fact ID for forget/doubt/correct target actions."},
            "target_id": {"type": "integer", "description": "Memory ID to correct (L2)."},
            "new_value": {"type": "string", "description": "Corrected value (for correct action)."},
            "new_confidence": {"type": "number", "description": "Confidence for corrected memory."},
            "relation": {"type": "string", "description": "Edge relation for link action: related_to, supports, contradicts."},
            "dst_id": {"type": "integer", "description": "Destination memory ID for link action."},
            "memory_type": {"type": "string", "description": "Filter by memory_type: semantic, episodic, procedural."},
            "user_id": {"type": "string", "description": "Source user ID filter for search."},
            "workflow_name": {"type": "string", "description": "Workflow name."},
            "limit": {"type": "integer", "description": "Max results. Default 10.", "default": 10},
        },
        "required": ["action"],
    },
}

# Lazy import to avoid triggering full memory subsystem at module load time
_UnifiedMemoryGateway: Optional[type] = None


def _get_gateway():
    global _UnifiedMemoryGateway
    if _UnifiedMemoryGateway is None:
        from agent.memory.gateway import UnifiedMemoryGateway as UG
        _UnifiedMemoryGateway = UG
    return _UnifiedMemoryGateway.get_instance()


def memory_gateway_tool(
    action: str,
    query: str = "",
    category: str = "",
    key: str = "",
    value: str = "",
    tags: list = None,
    confidence: float = 0.5,
    fact_id: int = 0,
    target_id: int = 0,
    new_value: str = "",
    new_confidence: float = 0.85,
    relation: str = "",
    dst_id: int = 0,
    memory_type: str = "",
    user_id: str = "",
    workflow_name: str = "",
    limit: int = 10,
    **kwargs,
) -> str:
    """Handle memory_gateway tool calls."""

    gw = _get_gateway()

    try:
        if action == "recall":
            return _handle_recall(gw, query, limit)

        elif action == "remember":
            return _handle_remember(gw, category, key, value, tags, confidence)

        elif action == "correct":
            return _handle_correct(gw, target_id, new_value, new_confidence)

        elif action == "forget":
            return _handle_forget(gw, fact_id or 0)

        elif action == "doubt":
            return _handle_doubt(gw, fact_id or 0)

        elif action == "link":
            return _handle_link(gw, fact_id or 0, dst_id, relation)

        elif action == "search":
            return _handle_search(gw, query, memory_type, user_id, limit)

        elif action == "self_audit":
            return _handle_self_audit(gw, user_id, limit)

        elif action == "list_facts":
            return _handle_list_facts(gw, category, limit)

        elif action == "list_workflows":
            return _handle_list_workflows(gw)

        elif action == "use_workflow":
            return _handle_use_workflow(gw, workflow_name)

        elif action == "suggest_skill":
            return _handle_suggest_skill(gw)

        elif action == "wiki_search":
            return _handle_wiki_search(gw, query, limit)

        elif action == "obsidian_search":
            return _handle_obsidian_search(gw, query, limit)

        elif action == "obsidian_read":
            return _handle_obsidian_read(gw, query)

        elif action == "stats":
            return _handle_stats(gw)

        elif action == "consolidate":
            return _handle_consolidate(gw)

        elif action == "decay_report":
            return _handle_decay_report(gw)

        elif action == "timeline":
            return _handle_timeline(gw, query, limit)

        elif action == "core_remember":
            return _handle_core_remember(gw, category, value)

        elif action == "core_forget":
            return _handle_core_forget(gw, fact_id or 0)

        elif action == "core_list":
            return _handle_core_list(gw, category)

        else:
            return json.dumps({"error": f"Unknown action: {action}"})

    except Exception as e:
        logger.error("Memory gateway error (action=%s): %s", action, e, exc_info=True)
        return json.dumps({"error": str(e)})


def _handle_recall(gw: UnifiedMemoryGateway, query: str, limit: int) -> str:
    if not query:
        return json.dumps({"error": "query is required for recall"})

    structured = gw.recall_structured(query, max_chars=4000)
    context = structured["prompt"]
    recalled_ids = structured.get("recalled_ids", [])
    ltm_results = gw.search_long_term(query, limit)
    wfm_results = gw.search_workflows(query)
    wiki_results = gw._wiki.search(query, limit) if gw._enable_wiki else []

    return json.dumps({
        "context": context[:3000],
        "recalled_memory_ids": recalled_ids,
        "long_term_matches": ltm_results,
        "workflow_matches": wfm_results,
        "wiki_matches": [
            {"title": w.title, "section": w.section, "snippet": w.content[:200]}
            for w in wiki_results[:3]
        ],
    }, ensure_ascii=False)


def _handle_remember(gw: UnifiedMemoryGateway, category: str, key: str,
                     value: str, tags: list, confidence: float) -> str:
    if not key or not value:
        return json.dumps({"error": "key and value are required for remember"})

    if not category:
        category = "general"

    # Don't store low-confidence noise — these are usually auto-generated
    # fragments that never get retrieved and only pollute the memory.
    if confidence < 0.3:
        return json.dumps({"success": False, "rejected": True,
                           "reason": f"confidence too low ({confidence:.2f}) for storage"})

    entry_id = gw.add_long_term(
        category=category,
        key=key,
        value=value,
        tags=tags or [],
        confidence=min(1.0, max(0.0, confidence)),
    )
    return json.dumps({"success": True, "fact_id": entry_id, "action": "remember"})


def _handle_forget(gw: UnifiedMemoryGateway, fact_id: int) -> str:
    if not fact_id:
        return json.dumps({"error": "fact_id is required for forget"})

    gw.delete_long_term(fact_id)
    return json.dumps({"success": True, "action": "forget", "fact_id": fact_id})


def _handle_list_facts(gw: UnifiedMemoryGateway, category: str, limit: int) -> str:
    if category:
        results = gw._ltm.get_category(category, limit)
    else:
        results = gw._ltm.get_all(limit)

    return json.dumps({
        "count": len(results),
        "facts": [
            {
                "id": r.id,
                "category": r.category,
                "key": r.key,
                "value": r.value[:300],
                "confidence": r.confidence,
                "retrieval_count": r.retrieval_count,
            }
            for r in results
        ],
    }, ensure_ascii=False)


def _handle_list_workflows(gw) -> str:
    from agent.memory.workflow import DECAY_MIN_WEIGHT

    gw._wfm.apply_decay_all()
    wfs = gw._wfm._store.get_all_workflows()

    return json.dumps({
        "count": len(wfs),
        "active_threshold": DECAY_MIN_WEIGHT,
        "workflows": [
            {
                "name": w.name,
                "description": w.description[:200],
                "weight": round(w.current_weight, 4),
                "usage_count": w.usage_count,
                "success_rate": round(w.success_count / max(1, w.usage_count), 2),
                "last_used_days_ago": (
                    round((time.time() - w.last_used) / 86400.0, 1)
                    if w.last_used > 0 else "never"
                ),
                "status": (
                    "forgotten" if w.current_weight <= DECAY_MIN_WEIGHT
                    else "decaying" if w.current_weight < 0.3
                    else "active"
                ),
            }
            for w in wfs
        ],
    }, ensure_ascii=False)


def _handle_use_workflow(gw: UnifiedMemoryGateway, name: str) -> str:
    if not name:
        return json.dumps({"error": "workflow_name is required"})
    gw._wfm.record_usage(name, success=True)
    wf = gw._wfm._store.get_workflow(name)
    if wf:
        return json.dumps({
            "success": True,
            "workflow": name,
            "new_weight": round(wf.current_weight, 4),
            "usage_count": wf.usage_count,
        })
    return json.dumps({"success": False, "error": f"Workflow '{name}' not found"})


def _handle_suggest_skill(gw: UnifiedMemoryGateway) -> str:
    from agent.memory.skill_gen import SkillAutoGenerator

    gen = SkillAutoGenerator(gw)
    generated = gen.scan_and_generate()
    stats = gen.get_skill_stats()

    return json.dumps({
        "generated": [
            {"name": g["name"], "effectiveness": g["initial_effectiveness"],
             "workflow": g["workflow"]}
            for g in generated
        ],
        "existing_skills": stats,
        "total_auto_skills": len(stats),
    }, ensure_ascii=False)


def _handle_wiki_search(gw: UnifiedMemoryGateway, query: str, limit: int) -> str:
    if not query:
        return json.dumps({"error": "query is required for wiki_search"})

    if not gw._enable_wiki:
        return json.dumps({"error": "Wiki knowledge base is not enabled"})

    results = gw._wiki.search(query, limit)
    gw._wiki_synced = True

    return json.dumps({
        "count": len(results),
        "results": [
            {
                "title": r.title,
                "section": r.section,
                "snippet": r.content[:500],
                "source_url": r.source_url,
            }
            for r in results
        ],
    }, ensure_ascii=False)


def _handle_stats(gw: UnifiedMemoryGateway) -> str:
    stats = gw.get_stats()
    return json.dumps(stats, ensure_ascii=False)


def _handle_consolidate(gw) -> str:
    """Trigger consolidation for all active sessions.

    Consolidates every session currently tracked in the gateway's
    in-memory turn counter (sessions that have accumulated ≥
    CONSOLIDATION_MIN_TURNS turns since the process started). Returns a
    per-session breakdown so the agent can see which facts were promoted
    or reinforced.
    """
    from agent.memory.consolidation import CONSOLIDATION_MIN_TURNS

    sessions = list(gw._turn_counters.keys())
    if not sessions:
        store_stats = (gw.get_stats().get("store", {}))
        return json.dumps({
            "message": "No active sessions to consolidate.",
            "current_state": {
                "short_term_entries": store_stats.get("short_term_count", 0),
                "long_term_facts": store_stats.get("long_term_count", 0),
                "workflows": store_stats.get("workflow_count", 0),
            },
            "hint": "Send a few messages first — sessions need "
                    f"{CONSOLIDATION_MIN_TURNS}+ turns to consolidate.",
        })

    results = []
    for sid in sessions:
        stats = gw.consolidate_if_needed(sid)
        if stats is None:
            results.append({
                "session_id": sid,
                "status": "skipped",
                "reason": f"< {CONSOLIDATION_MIN_TURNS} turns",
            })
        else:
            results.append({
                "session_id": sid,
                **stats,
            })

    return json.dumps({
        "consolidated": results,
        "count": len([r for r in results if r.get("status") == "completed"]),
        "skipped": len([r for r in results if r.get("status") == "skipped"]),
    })


def _handle_decay_report(gw) -> str:
    report = gw.get_workflow_decay_report()
    return json.dumps({
        "total_workflows": len(report),
        "active": sum(1 for r in report if r["status"] == "active"),
        "decaying": sum(1 for r in report if r["status"] == "decaying"),
        "forgotten": sum(1 for r in report if r["status"] == "forgotten"),
        "workflows": report,
    }, ensure_ascii=False)


def _handle_timeline(gw, query: str = None, limit: int = None) -> str:
    """Return recent memories in time order, optionally filtered by query."""
    import time
    from datetime import datetime, timedelta

    days = 7
    cutoff = time.time() - days * 86400
    max_results = limit or 20

    # Recent conversation entries from STM (last 7 days)
    stm_entries = []
    try:
        conn = gw._store._get_conn()
        rows = conn.execute(
            "SELECT speaker_name, role, content, topics, emotional_tone, created_at "
            "FROM short_term_entries WHERE created_at > ? "
            "ORDER BY created_at DESC LIMIT ?",
            (cutoff, max_results),
        ).fetchall()
        for r in rows:
            entry = {
                "speaker": r[0] or "",
                "role": r[1],
                "content": r[2][:200] if r[2] else "",
                "topics": r[3],
                "emotion": r[4] or "",
                "time": datetime.fromtimestamp(r[5]).strftime("%m-%d %H:%M"),
            }
            if query and query.lower() not in entry["content"].lower():
                continue
            stm_entries.append(entry)
    except Exception as e:
        logger.debug("Timeline STM query failed: %s", e)

    # LTM facts (recent)
    ltm_facts = []
    try:
        conn = gw._store._get_conn()
        rows = conn.execute(
            "SELECT category, key, value, tags, confidence, created_at "
            "FROM long_term_entries WHERE created_at > ? "
            "ORDER BY created_at DESC LIMIT ?",
            (cutoff, max_results),
        ).fetchall()
        for r in rows:
            fact = {
                "category": r[0],
                "key": r[1],
                "value": r[2][:150] if r[2] else "",
                "tags": r[3],
                "confidence": r[4],
                "time": datetime.fromtimestamp(r[5]).strftime("%m-%d %H:%M"),
            }
            if query and query.lower() not in fact["value"].lower():
                continue
            ltm_facts.append(fact)
    except Exception as e:
        logger.debug("Timeline LTM query failed: %s", e)

    return json.dumps({
        "days": days,
        "stm_conversations": stm_entries[:max_results],
        "ltm_facts": ltm_facts[:max_results],
    }, ensure_ascii=False)


def _handle_obsidian_search(gw, query: str, limit: int) -> str:
    if not query:
        return json.dumps({"error": "query is required for obsidian_search"})

    try:
        gw.index_obsidian()
    except Exception as e:
        logger.warning("Obsidian indexing failed during obsidian_search: %s", e)

    results = gw.search_obsidian(query, top_k=limit or 5)

    # Add file modification times for time-based context
    import os, time
    from datetime import datetime
    vault = getattr(gw, "obsidian", None)
    knowledge_root = str(getattr(vault, "vault_path", "") or "").strip()

    enhanced = []
    has_knowledge_root = os.path.isdir(knowledge_root)
    for r in results:
        item = dict(r)
        item["snippet"] = item.get("snippet", "")[:800]
        # Find the actual file to get modification time
        title = item.get("title", "")
        if title:
            if not has_knowledge_root:
                enhanced.append(item)
                continue
            for root, dirs, files in os.walk(knowledge_root):
                for f in files:
                    if f.endswith(".md") and title in f:
                        fpath = os.path.join(root, f)
                        mtime = os.path.getmtime(fpath)
                        item["last_modified"] = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                        item["file"] = os.path.relpath(fpath, knowledge_root).replace("\\", "/")
                        break
                if "last_modified" in item:
                    break
        enhanced.append(item)

    stats = gw.get_obsidian_stats()

    return json.dumps({
        "query": query,
        "vault_stats": stats,
        "results": enhanced,
        "hint": "These are snippets only. To read a full note, use: memory_gateway(action='obsidian_read', query='EXACT_TITLE_FROM_ABOVE')",
    }, ensure_ascii=False)


def _handle_obsidian_read(gw, title: str) -> str:
    """Read the full content of a specific Obsidian note by title."""
    if not title:
        return json.dumps({"error": "title is required for obsidian_read"})

    try:
        gw.index_obsidian()
    except Exception as e:
        logger.warning("Obsidian indexing failed during obsidian_read: %s", e)

    note = gw.obsidian.get_note(title) if gw.obsidian else None
    if not note:
        return json.dumps({
            "error": f"Note not found: {title}",
            "hint": "Use obsidian_search to find the exact title first.",
        }, ensure_ascii=False)

    note.load()

    return json.dumps({
        "title": note.title,
        "path": note.rel_path,
        "tags": note.tags,
        "headings": note.headings,
        "length": len(note.content),
        "content": note.content,
    }, ensure_ascii=False)


def _handle_correct(gw, target_id: int, new_value: str, new_confidence: float) -> str:
    if not target_id or not new_value:
        return json.dumps({"error": "target_id and new_value are required for correct"})
    result = gw.correct(target_id, new_value, new_confidence)
    if result:
        return json.dumps(result, ensure_ascii=False)
    return json.dumps({"error": "correction failed"})

def _handle_doubt(gw, fact_id: int) -> str:
    if not fact_id:
        return json.dumps({"error": "fact_id is required for doubt"})
    result = gw.doubt(fact_id)
    return json.dumps(result, ensure_ascii=False)

def _handle_link(gw, src_id: int, dst_id: int, relation: str) -> str:
    if not src_id or not dst_id:
        return json.dumps({"error": "src_id and dst_id required for link"})
    result = gw.link(src_id, dst_id, relation or "related_to")
    if isinstance(result, dict) and result.get("edge_id"):
        return json.dumps(result, ensure_ascii=False)
    if isinstance(result, int) and result:
        return json.dumps({"edge_id": result}, ensure_ascii=False)
    return json.dumps({"error": "link failed"})

def _handle_search(gw, query: str, memory_type: str, user_id: str, limit: int) -> str:
    results = gw.search_long_term(query, limit)
    return json.dumps({"results": results}, ensure_ascii=False)

def _handle_self_audit(gw, user_id: str, limit: int) -> str:
    conflicts = gw.source_conflict_scan()
    return json.dumps({"conflicts": conflicts, "note": "v1: log only, no auto-correct"}, ensure_ascii=False)


def _handle_core_remember(gw, category: str, value: str) -> str:
    if not value:
        return json.dumps({"error": "value is required for core_remember"})
    cat = category or "general"
    mem_id = gw._store.add_core_memory(
        category=cat, content=value, source="self_write"
    )
    return json.dumps({"success": True, "core_memory_id": mem_id, "category": cat})


def _handle_core_forget(gw, fact_id: int) -> str:
    if not fact_id:
        return json.dumps({"error": "fact_id is required for core_forget"})
    ok = gw._store.soft_delete_core_memory(fact_id)
    return json.dumps({"success": ok, "soft_deleted": ok, "fact_id": fact_id})


def _handle_core_list(gw, category: str) -> str:
    rows = gw._store.list_core_memories(category=category or None)
    return json.dumps({"core_memories": rows, "count": len(rows)}, ensure_ascii=False)


def check_requirements() -> bool:
    return True


from tools.registry import registry

registry.register(
    name="memory_gateway",
    toolset="memory",
    schema=GATEWAY_SCHEMA,
    handler=lambda args, **kw: memory_gateway_tool(
        action=args.get("action", ""),
        query=args.get("query", ""),
        category=args.get("category", ""),
        key=args.get("key", ""),
        value=args.get("value", ""),
        tags=args.get("tags", []),
        confidence=args.get("confidence", 0.5),
        fact_id=args.get("fact_id", 0),
        target_id=args.get("target_id", 0),
        new_value=args.get("new_value", ""),
        new_confidence=args.get("new_confidence", 0.85),
        relation=args.get("relation", ""),
        dst_id=args.get("dst_id", 0),
        memory_type=args.get("memory_type", ""),
        user_id=args.get("user_id", ""),
        workflow_name=args.get("workflow_name", ""),
        limit=args.get("limit", 10),
    ),
    check_fn=check_requirements,
    emoji="🧠",
)
