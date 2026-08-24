import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .trigger_coordinator import TriggerRequest

logger = logging.getLogger(__name__)

# Runaway flood guard: a single _run_loop processes at most 10 turns before
# re-arming via schedule(); the FIFO queue itself is unbounded.
_MAX_LOOP_TURNS = 10


@dataclass
class AgentOutcome:
    kind: str = "sent"       # "sent" | "silent" | "quiet" | "failed"
    reply_text: str = ""     # actual reply text (for recorder)
    sent_message_ids: Tuple[str, ...] = ()


class GroupExecutor:
    """Phase 3 – single-flight agent runner.

    Only one runner exists per group at a time.  New trigger
    requests arriving while a runner is active are merged into
    the pending request (highest seq wins).  The runner loops
    as long as there are un-consumed user messages.
    """

    def __init__(self, adapter):
        self._adapter = adapter
        self._runners: Dict[str, asyncio.Task] = {}
        self._pending: Dict[str, list] = {}  # group_id -> [TriggerRequest] FIFO queue

    # ── public ──────────────────────────────────────────────

    def schedule(self, request):
        gid = request.group_id
        lst = self._pending.setdefault(gid, [])
        # Dedup by (origin_seq, mode) so re-schedules don't duplicate.
        if not any(getattr(r, "origin_seq", None) == request.origin_seq
                   and getattr(r, "mode", None) == request.mode for r in lst):
            lst.append(request)
        existing = self._runners.get(gid)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(self._run_loop(gid))
        self._runners[gid] = task

    # ── run loop ────────────────────────────────────────────

    async def _run_loop(self, group_id: str):
        try:
            # Process the WHOLE queue (FIFO) — a slow previous turn must not
            # silently drop later requests (single-slot pending used to keep
            # only the last one, losing intermediate mentions).  Cap at 10
            # turns per loop as a runaway flood guard.
            rounds = 0
            while rounds < _MAX_LOOP_TURNS:
                lst = self._pending.get(group_id)
                if not lst:
                    break
                request = lst.pop(0)
                await self._run_turn(request)
                rounds += 1

                gs = self._adapter._group_states.get(group_id)
                if not gs.is_attentive():
                    break

                if gs.last_user_seq > gs.last_consumed_seq:
                    if gs.episode_state.episode_phase == "exiting":
                        gs.go_quiet()
                        break
                    # Continuation fan-out: queue the next un-consumed user
                    # message only if not already queued.
                    if not any(getattr(r, "origin_seq", None) == gs.last_user_seq
                               for r in self._pending.get(group_id, [])):
                        self._pending.setdefault(group_id, []).append(TriggerRequest(
                            group_id=group_id,
                            origin_seq=gs.last_user_seq,
                            mode="continuation",
                            decision_reason="连续对话",
                        ))
                else:
                    break

            # Re-arm runner if leftover requests remain (e.g. flooded in
            # while this loop was finishing).  Only re-arm when we are the
            # registered runner — finally below must not clobber a newer
            # runner created by a concurrent schedule().
            if self._pending.get(group_id):
                if self._runners.get(group_id) is asyncio.current_task():
                    self._runners.pop(group_id, None)
                    self.schedule(self._pending[group_id][0])

        finally:
            if self._runners.get(group_id) is asyncio.current_task():
                self._runners.pop(group_id, None)

    async def _run_turn(self, request):
        gid = request.group_id
        lock = self._adapter._get_group_lock(gid)

        async with lock:
            gs = self._adapter._group_states.get(gid)

            latest_user = gs.latest_user_message()
            if latest_user and hasattr(self._adapter, '_media_pipeline'):
                await self._adapter._media_pipeline.await_completion(latest_user.seq)

            snapshot_seq = gs.last_user_seq
            snapshot = gs.snapshot(snapshot_seq)

            if hasattr(self._adapter, '_media_pipeline'):
                pending = [
                    m.seq for m in snapshot
                    if m.seq <= snapshot_seq and "[image:pending]" in (getattr(m, "text", "") or "")
                ]
                if pending:
                    await asyncio.gather(
                        *(self._adapter._media_pipeline.await_completion(seq) for seq in pending[:5])
                    )

            state_meta = gs.snapshot_meta()

            event = await self._build_event(request, snapshot, state_meta)
            if event is None:
                return

            outcome = await self._run_agent_locked(event)
            gs.mark_consumed(snapshot_seq)
            self._apply_outcome(gid, event, outcome, gs)

            if request.mode == "exit":
                # Last word delivered (or attempted) — leave the conversation.
                gs.go_quiet()

            if outcome.kind == "sent" and outcome.reply_text:
                await self._record_episode_state(request, gs, outcome)

            await self._adapter._update_rolling_summary(gid)

    # ── build event ─────────────────────────────────────────

    async def _build_event(self, request, snapshot, state_meta):
        group_id = request.group_id
        gs = self._adapter._group_states.get(group_id)

        sender = request.raw_msg.get("sender", {})
        sender_name = sender.get("card") or sender.get("nickname") or ""

        channel_prompt = await self._build_channel_prompt(
            group_id, request, snapshot, state_meta, sender_name,
        )

        from gateway.session import SessionSource
        user_id_str = str(request.raw_msg.get("user_id", ""))
        source = SessionSource(
            platform=self._adapter.platform,
            chat_id=f"group:{group_id}",
            user_id=user_id_str,
            user_name=sender_name,
            chat_type="group",
        )

        text = self._adapter._cq_to_readable(
            self._adapter._get_raw_text(request.raw_msg) or ""
        )

        from .adapter import MessageEvent, MessageType
        return MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=source,
            raw_message=request.raw_msg,
            message_id=str(request.raw_msg.get("message_id", "")),
            channel_prompt=channel_prompt,
        )

    async def _build_channel_prompt(
        self, group_id, request, snapshot, state_meta, sender_name,
    ):
        msg = request.raw_msg
        gs = self._adapter._group_states.get(group_id)

        context_image_paths: List[str] = []
        group_context = self._format_group_context(snapshot, context_image_paths, group_id, gs)

        trigger_reason = request.decision_reason
        if request.mode == "attentive":
            trigger_reason = "你在关注这段对话"
            mode = "[对话模式]"
        elif request.mode == "mention":
            trigger_reason = "该用户@了你"
            mode = "[对话模式]"
        elif request.mode == "continuation":
            mode = "[对话模式]"
            trigger_reason = "群里有新消息"
        elif request.mode == "judge":
            mode = "[对话模式]"
            trigger_reason = request.decision_reason
        elif request.mode == "exit":
            # Last word before leaving: reply (sass/farewell), then go quiet.
            mode = "[退出模式]"
            trigger_reason = "你被赶了，说最后一句（可以嘴硬/告别），说完就安静"
        else:
            mode = "[旁观模式]"

        msg_time = msg.get("time", 0)
        time_str = time.strftime('%m-%d %H:%M', time.localtime(msg_time)) if msg_time else ""
        raw_text = self._adapter._cq_to_readable(
            self._adapter._get_raw_text(msg) or ""
        )[:100]

        channel_prompt = f"{mode} {trigger_reason}。{time_str} 来自「{sender_name or '群友'}」：{raw_text}。"
        if group_context:
            channel_prompt += f"\n\n{group_context}"

        recall_ctx = self._adapter._recall_context(
            raw_text, str(msg.get("user_id", "")), sender_name,
            session_id=f"onebot:group:{group_id}",
        )
        if recall_ctx:
            channel_prompt += f"\n\n{recall_ctx}"

        try:
            from agent.memory.store import MemoryStore
            core = MemoryStore().load_core_memories_prompt()
            if core:
                channel_prompt += f"\n\n{core}"
        except Exception:
            pass

        if gs.rolling_summary:
            channel_prompt += f"\n\n[对话摘要] {gs.rolling_summary}"

        es = gs.episode_state
        if es.turn_count > 0:
            parts = []
            parts.append(f"[对话状态] 第{es.turn_count}轮")
            if es.episode_label:
                parts.append(f"话题: {es.episode_label}")
            if es.episode_phase:
                parts.append(f"阶段: {es.episode_phase}")
            if es.conversation_mode:
                parts.append(f"氛围: {es.conversation_mode}")
            if es.current_thread:
                parts.append(f"当前话题: {es.current_thread}")
            if es.progression_guidance and request.mode != "mention":
                parts.append(f"指导: {es.progression_guidance}")
            if es.overused_moves:
                parts.append(f"避免: {', '.join(es.overused_moves)}")
            if es.resolved_threads:
                parts.append(f"已聊完: {', '.join(es.resolved_threads)}")
            channel_prompt += "\n\n" + "\n".join(parts)

        if recall_ctx and "别处的印象" in recall_ctx:
            channel_prompt += (
                "\n\n[联想] 上面的「别处的印象」是你在**别的群/私聊**里听来的，"
                "不是本群的对话。可以像人一样自然地提起（比如「我记得好像有人说过」），"
                "但绝对不要说出是谁说的、在哪说的、什么时候在哪个群说的。"
                "如果跟当前话题其实不搭，就当没看见。"
            )

        channel_prompt += (
            "\n\n[工具] 你可以用以下标记控制行为：\n"
            "- 不想回话就只输出 [SILENT]（无其他文字），下次有人说话你还可以接\n"
            "- 觉得话题跟你完全没关系了就输出 [QUIET]（无其他文字），之后不再被叫到就不说话\n"
            "- 想引用某条消息就在回复里用 [reply:消息ID]\n"
            "\n[搜索历史] 你可以调用 search_chat_history 工具搜索群聊历史"
        )

        return self._limit_prompt_size(channel_prompt)

    def _format_group_context(self, snapshot, image_paths, group_id, gs):
        if not snapshot:
            return self._api_history_fallback(group_id, gs)

        lines = []
        for m in snapshot[-20:]:
            text = m.text
            ts = time.strftime('%m-%d %H:%M', time.localtime(m.ts))
            mid_tag = f"[mid:{m.mid}]" if m.mid else ""
            lines.append(f"{mid_tag}[{ts}] {m.name}({m.uid})" + (f": {text}" if text else ""))

        if lines:
            return "[群聊上下文]\n" + "\n".join(lines)

        if gs.is_episode_active() and gs.episode_start > 0:
            return self._db_history_fallback(group_id, gs.episode_start)
        return self._api_history_fallback(group_id, gs)

    def _api_history_fallback(self, group_id, gs):
        return ""

    def _db_history_fallback(self, group_id, episode_start):
        return ""

    def _limit_prompt_size(self, prompt: str):
        if len(prompt) > 500000:
            logger.info("[GroupExecutor] channel_prompt truncated to 500K")
        return prompt[-500000:] if len(prompt) > 500000 else prompt

    # ── agent execution ────────────────────────────────────

    async def _run_agent_locked(self, event) -> AgentOutcome:
        group_id = str(event.source.chat_id)
        fut = asyncio.get_event_loop().create_future()
        self._adapter._group_send_results[group_id] = fut
        try:
            # base.handle_message spawns a background task and returns
            # immediately (None) — its return value is NOT the reply.
            # Wait for the adapter's send() to resolve this group's
            # future with the actual reply text.  Timeout raised 45s→120s:
            # a slow LLM turn (30s+) + tool calls can exceed the old cap,
            # and a timeout here DROPS the reply (future popped unresolved
            # while the agent is still generating).  With 120s the signal
            # arrives before the cap in practice.
            await self._adapter.handle_message(event)
            try:
                reply_text = await asyncio.wait_for(fut, timeout=120)
            except asyncio.TimeoutError:
                logger.info("[GroupExecutor] send signal timeout for %s (background may still finish)", group_id)
                reply_text = ""
            return AgentOutcome(kind="sent", reply_text=reply_text)
        except Exception as e:
            logger.warning("[GroupExecutor] Agent failed: %s", e)
            return AgentOutcome(kind="failed", reply_text="")
        finally:
            self._adapter._group_send_results.pop(group_id, None)

    def _apply_outcome(self, group_id, event, outcome, gs):
        if outcome.kind == "sent":
            if gs.attentive.active:
                gs.record_reply()
            gs.last_agent_ts = time.time()
        elif outcome.kind == "silent":
            gs.record_silent()
            gs.last_agent_ts = time.time()
        elif outcome.kind == "quiet":
            gs.go_quiet()
            gs.last_agent_ts = time.time()

    async def _record_episode_state(self, request, gs, outcome):
        try:
            from .semantic_judge import post_reply_recorder

            recent_raw = gs.get_recent()
            recent_dicts = [
                {
                    "ts_str": time.strftime('%m-%d %H:%M', time.localtime(m.ts)),
                    "name": m.name, "text": m.text[:200], "is_bot": m.is_bot,
                }
                for m in recent_raw[-15:]
            ]

            speaker_role = getattr(request, "speaker_role", "") or "unknown"

            from .semantic_judge import _get_bot_name as _bot_name
            new_state = await post_reply_recorder(
                recent_messages=recent_dicts,
                bot_reply=outcome.reply_text,
                bot_name=_bot_name(),
                prior_episode_state=gs.episode_state.to_dict(),
                speaker_role=speaker_role,
            )

            from .group_state import EpisodeState
            old_turn = gs.episode_state.turn_count
            gs.episode_state = EpisodeState.from_dict(new_state)
            gs.episode_state.turn_count = old_turn + 1
            gs.episode_state.updated_at = time.time()

            logger.info(
                "[GroupExecutor] Episode state updated: phase=%s continuity=%s",
                new_state.get("episode_phase", "?"),
                new_state.get("continuity", "?"),
            )
        except Exception as e:
            logger.warning("[GroupExecutor] Episode recorder failed: %s", e)
