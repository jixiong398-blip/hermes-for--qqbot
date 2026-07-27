import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .trigger_coordinator import TriggerRequest

logger = logging.getLogger(__name__)

MAX_ROUNDS = 3


@dataclass
class AgentOutcome:
    kind: str = "sent"       # "sent" | "silent" | "quiet" | "failed"
    reply_text: str = ""
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
        self._pending: Dict[str, Any] = {}  # group_id -> TriggerRequest

    # ── public ──────────────────────────────────────────────

    def schedule(self, request):
        gid = request.group_id
        self._pending[gid] = request

        existing = self._runners.get(gid)
        if existing is not None and not existing.done():
            return

        task = asyncio.create_task(self._run_loop(gid))
        self._runners[gid] = task

    # ── run loop ────────────────────────────────────────────

    async def _run_loop(self, group_id: str):
        try:
            rounds = 0
            while rounds < MAX_ROUNDS:
                request = self._pending.pop(group_id, None)
                if request is None:
                    break

                await self._run_turn(request)
                rounds += 1

                gs = self._adapter._group_states.get(group_id)
                if not gs.is_attentive():
                    break

                if gs.last_user_seq > gs.last_consumed_seq:
                    self._pending[group_id] = TriggerRequest(
                        group_id=group_id,
                        origin_seq=gs.last_user_seq,
                        mode="continuation",
                        decision_reason="连续对话",
                    )
                else:
                    break

            if group_id in self._pending:
                self.schedule(self._pending.pop(group_id))

        finally:
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
            state_meta = gs.snapshot_meta()

            event = await self._build_event(request, snapshot, state_meta)
            if event is None:
                return

            outcome = await self._run_agent_locked(event)
            gs.mark_consumed(snapshot_seq)
            self._apply_outcome(gid, event, outcome, gs)

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
        try:
            await self._adapter.handle_message(event)
            return AgentOutcome(kind="sent")
        except Exception as e:
            logger.warning("[GroupExecutor] Agent failed: %s", e)
            return AgentOutcome(kind="failed")

    def _apply_outcome(self, group_id, event, outcome, gs):
        if outcome.kind == "sent":
            gs.record_reply()
            gs.last_agent_ts = time.time()
        elif outcome.kind == "silent":
            gs.record_silent()
            gs.last_agent_ts = time.time()
        elif outcome.kind == "quiet":
            gs.go_quiet()
            gs.last_agent_ts = time.time()
