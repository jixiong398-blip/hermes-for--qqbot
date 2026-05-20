"""Built-in memory recording + maintenance hook.

Records every conversation turn into Short-Term Memory and the Layer 0
event stream. Triggers consolidation on session end. Runs periodic
maintenance (decay, pruning, cleanup).

Events handled:
  agent:start      → Record user message in STM + Layer 0 JSONL
  agent:end        → Record assistant response in STM + Layer 0 JSONL
  session:end      → Run STM→LTM consolidation
  gateway:startup  → Start periodic maintenance timer

All operations are best-effort — failures are logged but never block
the message pipeline.
"""

import asyncio
import logging

logger = logging.getLogger("hooks.memory_maintenance")

_MAINTENANCE_INTERVAL_HOURS = 1
_MAINTENANCE_TASK = None
_MEMORY_GW = None
_GW_LOCK = None


def _get_gateway():
    """Lazy-init the UnifiedMemoryGateway singleton."""
    global _MEMORY_GW, _GW_LOCK
    if _MEMORY_GW is None:
        import threading
        if _GW_LOCK is None:
            _GW_LOCK = threading.Lock()
        with _GW_LOCK:
            if _MEMORY_GW is None:
                try:
                    from agent.memory.gateway import UnifiedMemoryGateway
                    _MEMORY_GW = UnifiedMemoryGateway.get_instance()
                    logger.info("UnifiedMemoryGateway initialized")
                except Exception as e:
                    logger.warning("Memory gateway init failed: %s", e)
                    _MEMORY_GW = False
    return _MEMORY_GW if _MEMORY_GW is not False else None


# ── Main handler ───────────────────────────────────────────

async def handle(event_type: str, context: dict) -> None:
    """Route events to handlers."""
    try:
        if event_type == "agent:start":
            await _on_agent_start(context)
        elif event_type == "agent:end":
            await _on_agent_end(context)
        elif event_type == "session:end":
            await _on_session_end(context)
        elif event_type == "gateway:startup":
            await _on_gateway_startup(context)
    except Exception as e:
        logger.debug("Memory hook %s error: %s", event_type, e)


# ── Turn recording ─────────────────────────────────────────

async def _on_agent_start(context: dict) -> None:
    """Record user message in STM and Layer 0 event stream."""
    gw = _get_gateway()
    if gw is None:
        return

    session_id = context.get("session_id", "")
    message = context.get("message", "")
    if not session_id or not message:
        return

    platform = context.get("platform", "")
    user_id = context.get("user_id", "")
    chat_type = "group" if context.get("chat_id") and "group" in str(context.get("chat_id", "")) else "dm"

    try:
        gw.process_turn(
            session_id=session_id,
            role="user",
            content=message,
            speaker_name=str(user_id),
            chat_type=chat_type,
            bot_replied=True,
        )
    except Exception:
        pass

    # Layer 0 event stream
    try:
        from agent.memory.event_stream import write_message
        write_message(
            session_id=session_id,
            role="user",
            content=message,
            speaker_name=str(user_id),
            platform=platform,
            chat_type=chat_type,
        )
    except Exception:
        pass


async def _on_agent_end(context: dict) -> None:
    """Record assistant response in STM and Layer 0 event stream."""
    gw = _get_gateway()
    if gw is None:
        return

    session_id = context.get("session_id", "")
    response = context.get("response", "")
    if not session_id or not response or response == "(empty)":
        return

    platform = context.get("platform", "")
    chat_type = "group" if context.get("chat_id") and "group" in str(context.get("chat_id", "")) else "dm"

    try:
        gw.process_turn(
            session_id=session_id,
            role="assistant",
            content=response,
            speaker_name="soyo",
            chat_type=chat_type,
            bot_replied=True,
        )
    except Exception:
        pass

    # Layer 0 event stream
    try:
        from agent.memory.event_stream import write_message
        write_message(
            session_id=session_id,
            role="assistant",
            content=response,
            speaker_name="soyo",
            platform=platform,
            chat_type=chat_type,
        )
    except Exception:
        pass


# ── Session-end consolidation ──────────────────────────────

async def _on_session_end(context: dict) -> None:
    """Run STM→LTM consolidation when a session ends."""
    gw = _get_gateway()
    if gw is None:
        return

    session_key = context.get("session_key", "")
    session_id = context.get("session_id", "")
    target = session_id or session_key
    if not target:
        return

    try:
        stats = gw.consolidate_if_needed(target)
        if stats and stats.get("status") != "skipped":
            logger.info(
                "Consolidation: promoted=%d reinforced=%d wf=%d",
                stats.get("facts_promoted", 0),
                stats.get("facts_reinforced", 0),
                stats.get("workflows_suggested", 0),
            )
    except Exception as e:
        logger.debug("Consolidation failed: %s", e)


# ── Periodic maintenance ───────────────────────────────────

async def _on_gateway_startup(context: dict) -> None:
    """Launch the periodic memory maintenance background task."""
    global _MAINTENANCE_TASK

    _get_gateway()  # warm up

    if _MAINTENANCE_TASK is not None:
        return

    async def _maintenance_loop():
        await asyncio.sleep(120)  # initial delay
        while True:
            try:
                gw = _get_gateway()
                if gw:
                    stats = gw.maintenance_cycle()
                    pruned = stats.get("stm_pruned", 0)
                    decayed = len(stats.get("workflow_decay", []))
                    if pruned > 0 or decayed > 0:
                        logger.info(
                            "Memory maintenance: pruned=%d STM, %d workflows decayed",
                            pruned, decayed,
                        )
            except Exception as e:
                logger.debug("Maintenance cycle failed: %s", e)
            await asyncio.sleep(_MAINTENANCE_INTERVAL_HOURS * 3600)

    try:
        _MAINTENANCE_TASK = asyncio.create_task(_maintenance_loop())
        logger.info("Memory maintenance timer started (every %dh)", _MAINTENANCE_INTERVAL_HOURS)
    except RuntimeError:
        logger.debug("Memory maintenance timer deferred (no event loop)")
