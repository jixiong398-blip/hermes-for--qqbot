"""
Unified Memory Gateway Tool — LLM-facing interface for the memory system.

Exposes the UnifiedMemoryGateway to the agent via a single `memory_gateway` tool.
This supersedes the old `memory` tool with unified recall, workflow management,
wiki search, and skill auto-generation.

Actions:
  - recall: Search all memory sources (STM, LTM, WFM, Wiki) for relevant context
  - remember: Save a fact to long-term memory
  - forget: Remove a fact from long-term memory
  - list_facts: List facts by category
  - list_workflows: List active workflows with weights
  - use_workflow: Record workflow usage (triggers weight boost)
  - suggest_skill: Suggest auto-generating a skill from workflows
  - wiki_search: Search the Karpathy Wiki knowledge base
  - stats: Get memory system statistics
  - consolidate: Manually trigger STM→LTM consolidation
  - buffer_search: Search raw chat history (chat_message_buffer) by keyword and time window
  - buffer_recent: Get recent chat buffer entries for a group (time window, for context reload)
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
        "buffer_search, buffer_recent."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "recall", "remember", "forget", "list_facts",
                    "list_workflows", "use_workflow", "suggest_skill",
                    "wiki_search", "obsidian_search", "obsidian_read",
                    "stats", "consolidate", "decay_report",
                    "timeline", "buffer_search", "buffer_recent",
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
            "fact_id": {"type": "integer", "description": "Fact ID for forget action."},
            "workflow_name": {"type": "string", "description": "Workflow name."},
            "limit": {"type": "integer", "description": "Max results. Default 10.", "default": 10},
            "chat_id": {"type": "string", "description": "Group ID for buffer_search/buffer_recent (QQ group number)."},
            "minutes": {"type": "integer", "description": "Time window in minutes for buffer_recent. Default 5.", "default": 5},
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
    workflow_name: str = "",
    limit: int = 10,
    chat_id: str = "",
    minutes: int = 5,
    **kwargs,
) -> str:
    """Handle memory_gateway tool calls."""

    gw = _get_gateway()

    try:
        if action == "recall":
            return _handle_recall(gw, query, limit)

        elif action == "remember":
            return _handle_remember(gw, category, key, value, tags, confidence)

        elif action == "forget":
            return _handle_forget(gw, fact_id or 0)

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

        elif action == "buffer_search":
            return _handle_buffer_search(query, chat_id, limit)

        elif action == "buffer_recent":
            return _handle_buffer_recent(chat_id, minutes, limit)

        else:
            return json.dumps({"error": f"Unknown action: {action}"})

    except Exception as e:
        logger.error("Memory gateway error (action=%s): %s", action, e, exc_info=True)
        return json.dumps({"error": str(e)})


def _handle_recall(gw: UnifiedMemoryGateway, query: str, limit: int) -> str:
    if not query:
        return json.dumps({"error": "query is required for recall"})

    context = gw.recall(query, max_chars=4000)
    # Also get direct search results
    ltm_results = gw.search_long_term(query, limit)
    wfm_results = gw.search_workflows(query)
    wiki_results = gw._wiki.search(query, limit) if gw._enable_wiki else []

    return json.dumps({
        "context": context[:3000],
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

    # Filter out joke nicknames — these are group chat banter, not real identity
    _joke_triggers = ["妈妈", "妈咪", "素世妈妈", "老婆", "女朋友", "女友", "女仆", "主人"]
    _combined = f"{key} {value}".lower()
    for _jt in _joke_triggers:
        if _jt.lower() in _combined:
            return json.dumps({"success": False, "rejected": True,
                               "reason": "joke nickname detected — not storing as fact"})

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
    """Trigger consolidation for all active sessions."""
    from agent.memory.consolidation import CONSOLIDATION_MIN_TURNS

    stats = gw.get_stats()
    store_stats = stats.get("store", {})

    return json.dumps({
        "message": (
            "Consolidation runs at session boundaries when a session accumulates "
            f"{CONSOLIDATION_MIN_TURNS}+ turns. Short-term patterns are promoted "
            "to long-term memory and workflow candidates are detected."
        ),
        "current_state": {
            "short_term_entries": store_stats.get("short_term_count", 0),
            "long_term_facts": store_stats.get("long_term_count", 0),
            "workflows": store_stats.get("workflow_count", 0),
            "wiki_chunks": store_stats.get("wiki_chunk_count", 0),
        },
        "hint": "Use 'recall' with a query to search memories, or 'decay_report' to see workflow health.",
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
    knowledge_root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "knowledge")
    if not os.path.isdir(knowledge_root):
        knowledge_root = r"E:\ai\knowledge"

    enhanced = []
    for r in results:
        item = dict(r)
        item["snippet"] = item.get("snippet", "")[:800]
        # Find the actual file to get modification time
        title = item.get("title", "")
        if title:
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


# ── Buffer Search (chat_message_buffer) ─────────────────────

def _get_buffer_db():
    """Get a connection to the state DB (master database for chat buffer)."""
    import sqlite3
    from pathlib import Path
    db_path = Path.home() / ".hermes" / "state.db"
    return sqlite3.connect(str(db_path))


def _handle_buffer_search(query: str, chat_id: str, limit: int) -> str:
    """Search chat_message_buffer by keyword, optionally scoped to a group."""
    if not query:
        return json.dumps({"error": "query is required for buffer_search"})

    try:
        db = _get_buffer_db()
        params = []
        sql = (
            "SELECT sender_name, content, created_at, chat_id FROM chat_message_buffer "
            "WHERE content LIKE ? "
        )
        params.append(f"%{query}%")

        if chat_id:
            sql += "AND chat_id = ? "
            params.append(chat_id)

        sql += "ORDER BY id DESC LIMIT ?"
        params.append(min(limit, 50))

        rows = db.execute(sql, params).fetchall()
        db.close()

        results = []
        for r in rows:
            from datetime import datetime
            ts = datetime.fromtimestamp(r[2]).strftime('%m-%d %H:%M')
            results.append({
                "sender": r[0],
                "text": r[1][:300],
                "time": ts,
                "group": r[3],
            })

        return json.dumps({
            "found": len(results),
            "results": results,
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": str(e)})


def _handle_buffer_recent(chat_id: str, minutes: int, limit: int) -> str:
    """Get recent messages from chat_message_buffer within a time window."""
    if not chat_id:
        return json.dumps({"error": "chat_id is required for buffer_recent"})

    try:
        db = _get_buffer_db()
        cutoff = time.time() - (minutes * 60)

        rows = db.execute(
            "SELECT sender_name, content, created_at FROM chat_message_buffer "
            "WHERE chat_id = ? AND created_at > ? "
            "AND lower(sender_name) NOT LIKE '%bot%' "
            "ORDER BY id DESC LIMIT ?",
            (chat_id, cutoff, min(limit, 100)),
        ).fetchall()
        db.close()

        import datetime
        results = []
        for r in reversed(rows):
            ts = datetime.datetime.fromtimestamp(r[2]).strftime('%m-%d %H:%M')
            results.append(f"[{ts}] {r[0]}: {r[1][:300]}")

        return json.dumps({
            "group": chat_id,
            "window_minutes": minutes,
            "messages": results,
            "count": len(results),
        }, ensure_ascii=False)

    except Exception as e:
        return json.dumps({"error": str(e)})


# ── Registry ──────────────────────────────────────────────────
from tools.registry import registry


def check_requirements() -> bool:
    """Always available — uses local SQLite only."""
    return True


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
        workflow_name=args.get("workflow_name", ""),
        limit=args.get("limit", 10),
        chat_id=args.get("chat_id", ""),
        minutes=args.get("minutes", 5),
    ),
    check_fn=check_requirements,
    emoji="🧠",
)
