import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

JUDGE_DEBOUNCE_SECONDS = 1.0
MENTION_BATCH_DELAY = 3.0
JUDGE_TIMEOUT = 30.0


@dataclass
class TriggerRequest:
    group_id: str
    origin_seq: int
    mode: str           # "attentive" | "mention" | "judge" | "continuation"
    decision_reason: str
    raw_msg: dict = field(default_factory=dict)
    is_mention: bool = False
    bundled_mentions: List[dict] = field(default_factory=list)
    bundled_seqs: List[int] = field(default_factory=list)


class _JudgeTask:
    """Internal tracking object for one in-flight judge lifecycle."""

    def __init__(self, group_id: str, epoch: int, initial_seq: int):
        self.group_id = group_id
        self.epoch = epoch
        self.initial_seq = initial_seq
        self.task: Optional[asyncio.Task] = None


class _MentionBatch:
    def __init__(self, group_id: str, delay: float = MENTION_BATCH_DELAY):
        self.group_id = group_id
        self.delay = delay
        self.deadline = time.monotonic() + delay
        self.entries: List[dict] = []
        self.msg_list: List[dict] = []
        self.seqs: List[int] = []
        self.task: Optional[asyncio.Task] = None

    def extend(self, entry: dict, msg: dict, seq: int):
        self.deadline = time.monotonic() + self.delay
        self.entries.append(entry)
        self.msg_list.append(msg)
        self.seqs.append(seq)

    def take_all(self) -> Tuple[List[dict], List[dict], List[int]]:
        entries = list(self.entries)
        msgs = list(self.msg_list)
        seqs = list(self.seqs)
        self.entries.clear()
        self.msg_list.clear()
        self.seqs.clear()
        return entries, msgs, seqs


class TriggerCoordinator:
    """Phase 2 – trigger decision for group messages.

    Owns:
      - judge timers (full lifecycle: debounce + LLM call)
      - mention deadline-batching (no cancel, moving deadline)
      - decision_epoch invalidation

    Output: TriggerRequest objects consumed by GroupExecutor.
    """

    def __init__(self, adapter):
        self._adapter = adapter
        self._judge_tasks: Dict[str, _JudgeTask] = {}
        self._mention_batches: Dict[str, _MentionBatch] = {}
        self._pending_requests: Dict[str, TriggerRequest] = {}

    # ── entry point ─────────────────────────────────────────

    async def on_ingested(
        self,
        group_id: str,
        seq: int,
        msg: dict,
        sender_name: str,
        raw_text: str,
    ):
        gs = self._adapter._group_states.get(group_id)

        if gs.is_attentive():
            self._schedule_judge(group_id, seq, msg)
            return

        is_mentioned = msg.get("_is_mentioned", False)

        if is_mentioned:
            self._enqueue_mention(group_id, seq, msg, sender_name, raw_text)
            return

        self._schedule_judge(group_id, seq, msg)

    # ── judge timer ─────────────────────────────────────────

    def _schedule_judge(self, group_id: str, seq: int, msg: dict):
        existing = self._judge_tasks.get(group_id)
        if existing is not None and existing.task is not None and not existing.task.done():
            logger.debug("[TriggerCoordinator] Judge already in-flight for %s, seq=%d waits", group_id, seq)
            return

        gs = self._adapter._group_states.get(group_id)
        epoch = gs.decision_epoch
        jt = _JudgeTask(group_id, epoch, seq)
        jt.task = asyncio.create_task(self._judge_worker(jt, msg))
        self._judge_tasks[group_id] = jt

    async def _judge_worker(self, jt: _JudgeTask, msg: dict):
        task = asyncio.current_task()
        try:
            await asyncio.sleep(JUDGE_DEBOUNCE_SECONDS)

            gs = self._adapter._group_states.get(jt.group_id)

            if jt.epoch != gs.decision_epoch:
                logger.debug("[TriggerCoordinator] Judge epoch changed for %s, discarding", jt.group_id)
                return

            latest_user = gs.latest_user_message()
            if latest_user is None:
                return
            current_seq = latest_user.seq

            gs.mark_judged(current_seq)
            gs.increment_decision_epoch()

            result = await self._invoke_judge(jt.group_id, latest_user, msg)
            if result is None:
                return

            epoch_after = gs.decision_epoch
            if epoch_after != jt.epoch + 1:
                logger.debug("[TriggerCoordinator] Judge result stale for %s, discarding", jt.group_id)
                return

            if result.get("should_end"):
                gs.end_episode()
                self._adapter._write_episodic_segment(jt.group_id)
                self._adapter._generate_group_topic_summary(jt.group_id)
                logger.info("[TriggerCoordinator] Judge ended episode for %s", jt.group_id)
            elif result.get("soyo_should_exit"):
                from .group_state import EpisodeState
                old_turn = gs.episode_state.turn_count
                gs.episode_state = EpisodeState.from_dict(result)
                gs.episode_state.turn_count = old_turn
                gs.episode_state.updated_at = time.time()
                gs.go_quiet()
                logger.info("[TriggerCoordinator] Soyo exiting for %s: %s",
                            jt.group_id, result.get("exit_reason", result.get("reason", ""))[:40])
            elif result.get("should_reply"):
                old_turn = gs.episode_state.turn_count
                gs.episode_state = EpisodeState.from_dict(result)
                gs.episode_state.turn_count = old_turn
                gs.episode_state.updated_at = time.time()
                self._submit_request(TriggerRequest(
                    group_id=jt.group_id,
                    origin_seq=current_seq,
                    mode="judge",
                    decision_reason=result.get("reason", "语义判定需要回复")[:30],
                    raw_msg=msg,
                ))
            else:
                logger.info("[TriggerCoordinator] Judge: no reply for %s", jt.group_id)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("[TriggerCoordinator] Judge worker error: %s", e)
        finally:
            cur = self._judge_tasks.get(jt.group_id)
            if cur is jt:
                self._judge_tasks.pop(jt.group_id, None)

    async def _invoke_judge(self, group_id: str, latest_user, msg: dict) -> Optional[Dict[str, Any]]:
        try:
            from .semantic_judge import pre_reply_judge as _sj
        except ImportError:
            return None

        gs = self._adapter._group_states.get(group_id)
        recent_raw = gs.get_recent()

        recent_no_current = [m for m in recent_raw if m.seq < latest_user.seq]
        recent_dicts = [
            {"ts_str": time.strftime('%m-%d %H:%M', time.localtime(m.ts)),
             "name": m.name, "text": m.text[:200], "is_bot": m.is_bot,
             "is_at": False}
            for m in recent_no_current[-10:]
        ]

        is_mention = msg.get("_is_mentioned", False)
        raw_text = self._adapter._cq_to_readable(self._adapter._get_raw_text(msg) or "")

        _self_id_str = str(self._adapter._self_id) if self._adapter._self_id else ""
        if _self_id_str and raw_text:
            import re
            raw_text = re.sub(r'@QQ' + re.escape(_self_id_str) + r'(?!\d)', '@Soyo', raw_text)
            raw_text = re.sub(r'@QQ\d+(?!\d)', '@群友', raw_text)

        msg_type_str = "text"
        if self._adapter._has_image_message(msg):
            msg_type_str = "image"
        elif self._adapter._has_voice_message(msg):
            msg_type_str = "voice"

        current_dict = {
            "ts_str": time.strftime('%m-%d %H:%M', time.localtime(msg.get("time", 0) or time.time())),
            "name": latest_user.name, "text": raw_text[:300],
            "msg_type": msg_type_str, "is_at": is_mention,
        }

        reply_to_name, reply_to_uid = self._resolve_reply(msg)

        attentive_state = "对话态" if gs.is_attentive() else ("旁观态" if gs.is_episode_active() else "潜水")

        episode_state_dict = gs.episode_state.to_dict() if gs.episode_state.turn_count > 0 else None

        try:
            return await _sj(
                recent_messages=recent_dicts,
                current_msg=current_dict,
                group_name="",
                attentive_state=attentive_state,
                episode_state=episode_state_dict,
                timeout=JUDGE_TIMEOUT,
                reply_to_name=reply_to_name,
                reply_to_uid=reply_to_uid,
                bot_self_id=_self_id_str,
            )
        except Exception as e:
            logger.warning("[TriggerCoordinator] Judge invoke failed: %s", e)
            return None

    def _resolve_reply(self, msg: dict) -> Tuple[str, str]:
        from .adapter import get_state_db_path
        reply_to_name = ""
        reply_to_uid = ""
        reply_msg_id = self._adapter._get_reply_message_id(msg) if not msg.get("_skip_reply_context") else None
        if reply_msg_id:
            try:
                import sqlite3 as _sql
                db = _sql.connect(str(get_state_db_path()), timeout=5)
                row = db.execute(
                    "SELECT sender_name, user_id FROM corpus_messages WHERE message_id=? ORDER BY id DESC LIMIT 1",
                    (str(reply_msg_id),),
                ).fetchone()
                db.close()
                if row:
                    reply_to_name = row[0] or ""
                    reply_to_uid = str(row[1] or "")
            except Exception:
                pass
        return reply_to_name, reply_to_uid

    # ── mention batching ────────────────────────────────────

    def _enqueue_mention(self, group_id: str, seq: int, msg: dict, name: str, text: str):
        batch = self._mention_batches.get(group_id)
        if batch is None:
            batch = _MentionBatch(group_id)
            self._mention_batches[group_id] = batch

        entry = {"name": name, "text": text, "msg": msg}
        batch.extend(entry, msg, seq)

        if batch.task is None or batch.task.done():
            batch.task = asyncio.create_task(self._mention_batch_worker(batch))

    async def _mention_batch_worker(self, batch: _MentionBatch):
        try:
            while True:
                now = time.monotonic()
                remaining = batch.deadline - now
                if remaining <= 0:
                    break
                await asyncio.sleep(min(remaining, 0.5))
                if batch.deadline > time.monotonic():
                    continue

            entries, msgs, seqs = batch.take_all()
            if not entries:
                return

            gs = self._adapter._group_states.get(batch.group_id)
            if gs is not None and self._batch_has_dismissal(entries):
                last_msg = msgs[-1] if msgs else {}
                self._schedule_judge(batch.group_id, seqs[-1], last_msg)
                return

            self._adapter._group_states.get(batch.group_id).enter_attentive()

            self._submit_request(TriggerRequest(
                group_id=batch.group_id,
                origin_seq=seqs[-1] if seqs else 0,
                mode="mention",
                decision_reason="该用户@了你",
                raw_msg=msgs[-1] if msgs else {},
                is_mention=True,
                bundled_mentions=entries,
                bundled_seqs=seqs,
            ))
        finally:
            self._mention_batches.pop(batch.group_id, None)

    # ── submit to executor ──────────────────────────────────

    def _batch_has_dismissal(self, entries: list) -> bool:
        _DISMISSAL_PATTERNS = [
            "去玩吧", "一边去", "别说了", "闭嘴", "够了",
            "退下", "安静点", "stop", "行了别",
            "滚", "走开", "别吵", "消停",
        ]
        for entry in entries:
            text = entry.get("text", "")
            for pat in _DISMISSAL_PATTERNS:
                if pat in text:
                    return True
        return False

    def _submit_request(self, request: TriggerRequest):
        self._pending_requests[request.group_id] = request
        self._adapter._schedule_group_run(request)
