"""Built-in memory recording + maintenance hook.

Records every conversation turn into Short-Term Memory and the Layer 0
event stream. Triggers consolidation on session end. Runs periodic
maintenance (distillation, decay, pruning, cleanup).

Events handled:
  agent:start      → Record user message in STM + Layer 0 JSONL
  agent:end        → Record assistant response in STM + Layer 0 JSONL
  session:end      → Run STM→LTM consolidation
  gateway:startup  → Start periodic maintenance timer (hourly distill + daily sleep)

All operations are best-effort — failures are logged but never block
the message pipeline.
"""

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger("hooks.memory_maintenance")

_MAINTENANCE_INTERVAL_HOURS = 1
_SLEEP_HOUR = 3
_MAINTENANCE_TASK = None
_MEMORY_GW = None
_GW_LOCK = None
_last_sleep_date = None


def _context_chat_type(context: dict) -> str:
    """Prefer Gateway's explicit source type over chat-id heuristics."""

    declared = str(context.get("chat_type") or "").strip().lower()
    if declared in {"dm", "group", "channel", "thread"}:
        return declared
    chat_id = str(context.get("chat_id") or "").lower()
    return "group" if "group" in chat_id else "dm"


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
    """Record the raw user event and optionally stage it for STM."""
    session_id = context.get("session_id", "")
    message = context.get("message", "")
    if not session_id or not message:
        return

    platform = context.get("platform", "")
    user_id = context.get("user_id", "")
    chat_type = _context_chat_type(context)
    speaker_name = str(context.get("user_name") or user_id or "")

    # A real Gateway turn is marked for deferred memory recording so an
    # interrupted/failed request cannot leave a user row in STM. Legacy direct
    # hook callers without this marker retain the historical best-effort write.
    if not context.get("_defer_memory_until_end"):
        gw = _get_gateway()
        if gw is not None:
            try:
                gw.process_turn(
                    session_id=session_id,
                    role="user",
                    content=message,
                    speaker_name=speaker_name,
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
            speaker_name=speaker_name,
            platform=platform,
            chat_type=chat_type,
            chat_id=context.get("chat_id", ""),
            thread_id=context.get("thread_id", ""),
        )
    except Exception:
        pass


async def _on_agent_end(context: dict) -> None:
    """Commit a completed turn to STM and Layer 0."""
    session_id = context.get("session_id", "")
    response = context.get("response", "")
    if not session_id or not response or response == "(empty)":
        return

    deferred = bool(context.get("_defer_memory_until_end"))
    if deferred and (
        not context.get("completed", False)
        or context.get("interrupted", False)
        or context.get("failed", False)
        or context.get("contract_retry", False)
    ):
        # Layer 0 has already captured the raw user event at agent:start, but
        # interrupted/failed turns are not durable STM conversational truth.
        return

    gw = _get_gateway()
    if gw is None:
        return

    platform = context.get("platform", "")
    chat_type = _context_chat_type(context)
    bot_name = str(context.get("bot_name") or "soyo")

    try:
        if deferred and context.get("message"):
            gw.process_turn(
                session_id=session_id,
                role="user",
                content=context.get("message", ""),
                speaker_name=str(
                    context.get("user_name")
                    or context.get("user_id")
                    or ""
                ),
                chat_type=chat_type,
                bot_replied=True,
            )
        gw.process_turn(
            session_id=session_id,
            role="assistant",
            content=response,
            speaker_name=bot_name,
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
            speaker_name=bot_name,
            platform=platform,
            chat_type=chat_type,
            chat_id=context.get("chat_id", ""),
            thread_id=context.get("thread_id", ""),
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
        stats = await asyncio.to_thread(gw.consolidate_if_needed, target)
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
        global _last_sleep_date
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

                    # Hourly distillation: consolidate every active session
                    # with enough accumulated turns. Without this, sessions
                    # that never get an explicit /reset (QQ group/private
                    # chat is the canonical case) keep accumulating STM rows
                    # but never promote facts to LTM/WFM — MEMORY.md freezes.
                    # consolidate_if_needed is idempotent: it marks rows
                    # summarized=1 so re-runs on the same turns are no-ops.
                    try:
                        conn = gw._store._get_conn()
                        rows = conn.execute(
                            "SELECT DISTINCT session_id FROM short_term_entries "
                            "WHERE summarized = 0"
                        ).fetchall()
                        for (sid,) in rows:
                            if not sid:
                                continue
                            # Blocking HTTP (requests.post ≤30s) — off the
                            # event loop so messages keep flowing.
                            cstats = await asyncio.to_thread(
                                gw.consolidate_if_needed, sid
                            )
                            if cstats and cstats.get("status") != "skipped":
                                logger.info(
                                    "Periodic distill [%s]: promoted=%d reinforced=%d wf=%d",
                                    sid[:20],
                                    cstats.get("facts_promoted", 0),
                                    cstats.get("facts_reinforced", 0),
                                    cstats.get("workflows_suggested", 0),
                                )
                    except Exception as e:
                        logger.debug("Periodic distill failed: %s", e)

                    now = datetime.now(timezone.utc)
                    today_str = now.strftime("%Y-%m-%d")
                    if _last_sleep_date != today_str and now.hour >= _SLEEP_HOUR:
                        try:
                            sleep_stats = gw.sleep_loop()
                            _last_sleep_date = today_str
                            logger.info(
                                "L3 sleep loop: episodes=%d corrections=%d facts=%d",
                                sleep_stats.get("episodes_processed", 0),
                                sleep_stats.get("corrections_triggered", 0),
                                sleep_stats.get("facts_promoted", 0),
                            )
                        except Exception as e:
                            logger.debug("Sleep loop failed: %s", e)
            except Exception as e:
                logger.debug("Maintenance cycle failed: %s", e)
            await asyncio.sleep(_MAINTENANCE_INTERVAL_HOURS * 3600)

    try:
        _MAINTENANCE_TASK = asyncio.create_task(_maintenance_loop())
        logger.info("Memory maintenance timer started (every %dh)", _MAINTENANCE_INTERVAL_HOURS)
    except RuntimeError:
        logger.debug("Memory maintenance timer deferred (no event loop)")
