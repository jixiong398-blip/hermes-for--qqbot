"""
Memory Gateway hooks for GatewayRunner integration.
QQ 群场景优化版 — 传递发言者身份和会话类型.
"""

from __future__ import annotations

import logging
import asyncio
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_gateway_cache: Any = None


def ensure_memory_gateway() -> Any:
    global _gateway_cache
    if _gateway_cache is None:
        from agent.memory.gateway import UnifiedMemoryGateway
        _gateway_cache = UnifiedMemoryGateway.get_instance()
    return _gateway_cache


def inject_memory_context(user_message: str, session_id: str,
                          chat_type: str = "dm") -> str:
    """获取记忆上下文, 注入到 agent prompt 前."""
    try:
        gw = ensure_memory_gateway()
        context = gw.get_context_for_agent(user_message, session_id, chat_type)
        if context:
            return f"\n\n<!-- memory-context -->\n{context}\n<!-- /memory-context -->\n"
    except Exception as e:
        logger.debug("Memory context injection failed: %s", e)
    return ""


def record_turn(session_id: str, role: str, content: str,
                speaker_name: str = "",
                chat_type: str = "dm",
                bot_replied: bool = True):
    """记录一轮对话.

    QQ 群场景:
      - 群友消息: role='user', speaker_name='群昵称', chat_type='group'
      - 自己回复后: role='assistant', chat_type='group', bot_replied=True
      - 潜水(未回复): 调用者应传 bot_replied=False 或直接跳过
    """
    try:
        gw = ensure_memory_gateway()
        gw.process_turn(
            session_id=session_id,
            role=role,
            content=content,
            speaker_name=speaker_name,
            chat_type=chat_type,
            bot_replied=bot_replied,
        )
    except Exception as e:
        logger.debug("Memory turn recording failed: %s", e)


def on_session_start(session_id: str):
    try:
        gw = ensure_memory_gateway()
        gw.on_session_start(session_id)
    except Exception as e:
        logger.debug("Memory session start failed: %s", e)


def on_session_end(session_id: str):
    try:
        gw = ensure_memory_gateway()
        gw.on_session_end(session_id)
    except Exception as e:
        logger.debug("Memory session end failed: %s", e)


def run_maintenance():
    try:
        gw = ensure_memory_gateway()
        stats = gw.maintenance_cycle()
        if stats.get("workflows_pruned"):
            logger.info("Memory maintenance: pruned %d workflows", len(stats["workflows_pruned"]))
        if stats.get("workflow_decay"):
            logger.debug("Memory maintenance: decayed %d workflows", len(stats["workflow_decay"]))
    except Exception as e:
        logger.debug("Memory maintenance failed: %s", e)


def sync_wiki():
    try:
        gw = ensure_memory_gateway()
        if gw._enable_wiki and not gw._wiki_synced:
            gw.sync_wiki()
            logger.info("Wiki sync completed: %s", gw._wiki.get_stats())
    except Exception as e:
        logger.warning("Wiki sync failed: %s", e)


def get_memory_stats() -> Dict[str, Any]:
    try:
        gw = ensure_memory_gateway()
        return gw.get_stats()
    except Exception:
        return {}


async def maintenance_loop(interval_seconds: float = 3600.0):
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            run_maintenance()
        except Exception as e:
            logger.debug("Maintenance loop error: %s", e)
