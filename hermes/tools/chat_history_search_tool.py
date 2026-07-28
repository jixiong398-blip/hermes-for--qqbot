"""Tool: search_chat_history — search OneBot QQ group chat corpus.

Registered under the ``session_search`` toolset so it's merged into the
``session_search`` configurable toolset via ``toolsets.get_toolset()``.
All platform toolsets that include ``session_search`` (via
``_HERMES_CORE_TOOLS``) automatically inherit this tool.

Returns compact ``[mid:...]`` results that the agent can cite via
``[reply:message_id]`` syntax in group chat replies.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from corpus_history import (
    _MAX_DB_LIMIT,
    _MAX_QUERY_CHARS,
    _DEFAULT_TOOL_LIMIT,
    init_fts,
    rebuild_fts,
    search_corpus,
)
from hermes_constants import get_state_db_path
from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

SEARCH_CHAT_HISTORY_SCHEMA = {
    "name": "search_chat_history",
    "description": (
        "搜索 QQ 群聊历史消息（语料库全文检索）。\n\n"
        "当你需要回忆群友说过什么话、之前讨论过什么话题、"
        "或者群友让你帮忙查找之前他说过的事情时，调用这个工具。\n\n"
        "使用场景：\n"
        "- 有人问「我之前说的那个...」「还记得上次...」\n"
        "- 需要翻看之前的讨论内容来回答问题\n"
        "- 群友提到一个你不在当前上下文里的话题\n\n"
        "返回结果中每条消息都带有 [mid:消息ID] 标签。"
        "你只能在回复中使用返回的真实 [mid:...] 值来引用消息，"
        "绝对不能自己编造消息 ID。"
        "引用时使用 [reply:消息ID] 格式。\n\n"
        "注意：\n"
        "- 短中文查询（少于3个字）会使用模糊匹配，结果可能不够精准\n"
        "- 搜索范围包括消息内容和发送者名称\n"
        "- 如果当前就是在这个群聊里，可以不传 group_id，工具会搜索所有群"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索关键词（最大200字符）。可以搜用户名或消息内容。中文短词用模糊匹配。",
            },
            "group_id": {
                "type": "string",
                "description": "可选：限定在某个群内搜索。不传则搜索所有群。在群聊里一般不用传，除非明确要找其他群的消息。",
            },
            "limit": {
                "type": "integer",
                "description": f"返回条数上限（默认{_DEFAULT_TOOL_LIMIT}，最大{_MAX_DB_LIMIT}）。",
                "default": _DEFAULT_TOOL_LIMIT,
            },
        },
        "required": ["query"],
    },
}


def search_chat_history(args: dict, **kwargs: Any) -> str:
    """Handle ``search_chat_history`` tool calls.

    Opens state.db, ensures FTS is idempotently initialised, and searches
    corpus_messages.  Returns compact JSON with ``[mid:...]`` tags.
    """
    query = (args.get("query") or "").strip()
    if not query:
        return tool_error("query is required and must be non-empty")

    limit = args.get("limit", _DEFAULT_TOOL_LIMIT)
    if not isinstance(limit, int):
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = _DEFAULT_TOOL_LIMIT
    limit = max(1, min(limit, _MAX_DB_LIMIT))

    group_id = args.get("group_id") or None
    db_path = str(get_state_db_path())

    try:
        init_fts(db_path)

        result = search_corpus(
            query=query,
            db_path=db_path,
            limit=limit,
            group_id=group_id,
        )

        try:
            from agent.memory.gateway import UnifiedMemoryGateway
            gw = UnifiedMemoryGateway.get_instance()
            episodes = gw.recall_episodes(query, session_id=None, limit=2)
            if episodes:
                result.setdefault("cross_group_memories", episodes)
        except Exception:
            pass

        return json.dumps(result, ensure_ascii=False)
    except sqlite3.Error as exc:
        logger.warning("search_chat_history sqlite error for query=%r: %s", query, exc)
        return tool_error(f"Search failed: {exc}")


# ── Registry ──────────────────────────────────────────────────────────────

registry.register(
    name="search_chat_history",
    toolset="session_search",
    schema=SEARCH_CHAT_HISTORY_SCHEMA,
    handler=search_chat_history,
    emoji="💬",
    description="搜索 QQ 群聊历史消息（全文检索，返回 [mid:...] 引用）",
)
