import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

JUDGE_DEBOUNCE_SECONDS = 1.0
JUDGE_TIMEOUT = 30.0

# Two-tier judgment windows: idle batches 5s into one judge call; attentive
# judges at 1s so nothing is missed. Topic shift starts a 15s grace countdown
# before going quiet (conversation may swing back).
JUDGE_WINDOW_IDLE_SECONDS = 5.0
JUDGE_WINDOW_ATTENTIVE_SECONDS = 1.0
EXIT_COUNTDOWN_SECONDS = 15.0


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
        self.pending_seq: Optional[int] = None
        self.pending_msg: Optional[dict] = None


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
        self._pending_requests: Dict[str, TriggerRequest] = {}
        self._exit_countdowns: Dict[str, float] = {}

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

        # @Soyo = enter attentive + strong-signal judge (1s window).
        # Mention BEFORE attentive — @ during episodes must never be
        # judged as noise and silently dropped.
        is_mentioned = msg.get("_is_mentioned", False)

        if is_mentioned:
            logger.info("[TriggerCoordinator] on_ingested MENTION group=%s seq=%s phase=%s",
                        group_id, seq, gs.episode_state.episode_phase if gs else "no-gs")
            # Hard signal: never queue behind an in-flight judge and never
            # get overwritten by a later pending message. Cancel the running
            # judge (its result would be stale anyway) and judge the mention
            # right away. The 1s attentive window still applies.
            existing = self._judge_tasks.get(group_id)
            if existing is not None and existing.task is not None and not existing.task.done():
                existing.task.cancel()
                existing.pending_seq = None
                existing.pending_msg = None
                self._judge_tasks.pop(group_id, None)
            gs = self._adapter._group_states.get(group_id)
            if gs is not None:
                old_phase = gs.episode_state.episode_phase or "empty"
                if old_phase in ("exiting", "winding_down", ""):
                    gs.episode_state.episode_phase = "starting"
                    gs.episode_state.progression_guidance = ""
                    gs.episode_state.episode_label = ""
                    gs.episode_state.conversation_mode = ""
                    gs.episode_state.updated_at = time.time()
                    logger.info("[TriggerCoordinator] Reset episode %s→starting for %s",
                                old_phase, group_id)
                gs.enter_attentive()
            self._exit_countdowns.pop(group_id, None)
            msg["_is_mentioned"] = True
            self._schedule_judge(group_id, seq, msg)
            return

        if gs.is_attentive():
            self._schedule_judge(group_id, seq, msg)
            return

        self._schedule_judge(group_id, seq, msg)

    # ── judge timer ─────────────────────────────────────────

    def _schedule_judge(self, group_id: str, seq: int, msg: dict):
        existing = self._judge_tasks.get(group_id)
        if existing is not None and existing.task is not None and not existing.task.done():
            # Judge in-flight: queue the newest message so it gets its own
            # judge round after the current one finishes (never silently drop).
            existing.pending_seq = seq
            existing.pending_msg = msg
            logger.debug("[TriggerCoordinator] Judge in-flight for %s, queued seq=%d", group_id, seq)
            return

        gs = self._adapter._group_states.get(group_id)
        epoch = gs.decision_epoch
        jt = _JudgeTask(group_id, epoch, seq)
        jt.task = asyncio.create_task(self._judge_worker(jt, msg))
        self._judge_tasks[group_id] = jt

    async def _judge_worker(self, jt: _JudgeTask, msg: dict):
        from .group_state import EpisodeState
        task = asyncio.current_task()
        try:
            gs = self._adapter._group_states.get(jt.group_id)
            # Two-tier window: attentive judges at 1s (nothing missed), idle
            # batches at 5s (cost saving in noisy channels).
            _window = (JUDGE_WINDOW_ATTENTIVE_SECONDS
                       if gs is not None and gs.is_attentive()
                       else JUDGE_WINDOW_IDLE_SECONDS)
            await asyncio.sleep(_window)

            if gs is None:
                return

            if jt.epoch != gs.decision_epoch:
                logger.debug("[TriggerCoordinator] Judge epoch changed for %s, discarding", jt.group_id)
                return

            # Exit countdown: topic shifted away, grace window expired —
            # leave attentive before judging so the next window is idle.
            _countdown = self._exit_countdowns.get(jt.group_id)
            if _countdown is not None and time.time() > _countdown:
                self._exit_countdowns.pop(jt.group_id, None)
                gs.go_quiet()
                logger.info("[TriggerCoordinator] Exit countdown expired for %s → idle",
                            jt.group_id)

            latest_user = gs.latest_user_message()
            if latest_user is None:
                return

            target_user = latest_user
            if msg.get("_is_mentioned"):
                # Judge the mentioned message itself — newer messages that
                # arrived during the window are background only, they must
                # not replace the @ request as the judged subject.
                for m in gs.get_recent():
                    if m.seq == jt.initial_seq:
                        target_user = m
                        break
                logger.info("[TriggerCoordinator] Mention judge: initial_seq=%s target_seq=%s is_at=%s text=%r",
                            jt.initial_seq, target_user.seq, target_user.at_self,
                            getattr(target_user, "text", "")[:40])
            current_seq = target_user.seq

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
            elif result.get("should_exit"):
                old_turn = gs.episode_state.turn_count
                gs.episode_state = EpisodeState.from_dict(result)
                gs.episode_state.turn_count = old_turn
                gs.episode_state.updated_at = time.time()
                if result.get("exit_farewell"):
                    # Judge explicitly wants a last word (sass/farewell) —
                    # reply once, then go quiet. Default is silent exit.
                    self._submit_request(TriggerRequest(
                        group_id=jt.group_id,
                        origin_seq=current_seq,
                        mode="exit",
                        decision_reason=result.get("exit_reason", result.get("reason", ""))[:30],
                        raw_msg=msg,
                        is_mention=bool(msg.get("_is_mentioned", False)),
                    ))
                    logger.info("[TriggerCoordinator] Soft exit (farewell) for %s: %s",
                                jt.group_id, result.get("exit_reason", result.get("reason", ""))[:40])
                else:
                    gs.go_quiet()
                    logger.info("[TriggerCoordinator] Bot exited for %s: %s",
                                jt.group_id, result.get("exit_reason", result.get("reason", ""))[:40])
            elif result.get("should_reply"):
                old_turn = gs.episode_state.turn_count
                gs.episode_state = EpisodeState.from_dict(result)
                gs.episode_state.turn_count = old_turn
                gs.episode_state.updated_at = time.time()
                self._exit_countdowns.pop(jt.group_id, None)
                self._submit_request(TriggerRequest(
                    group_id=jt.group_id,
                    origin_seq=current_seq,
                    mode="judge",
                    decision_reason=result.get("reason", "语义判定需要回复")[:30],
                    raw_msg=msg,
                    is_mention=bool(msg.get("_is_mentioned", False)),
                ))
            else:
                # no_reply. Topic shift while attentive starts the exit
                # countdown (grace period — conversation may swing back).
                if gs.is_attentive() and result.get("continuity") in ("sharp_transition", "related_shift"):
                    self._exit_countdowns[jt.group_id] = time.time() + EXIT_COUNTDOWN_SECONDS
                    logger.info("[TriggerCoordinator] Topic shift for %s — exit countdown %ds",
                                jt.group_id, EXIT_COUNTDOWN_SECONDS)
                logger.info("[TriggerCoordinator] Judge: no reply for %s", jt.group_id)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("[TriggerCoordinator] Judge worker error: %s", e)
        finally:
            cur = self._judge_tasks.get(jt.group_id)
            if cur is jt:
                self._judge_tasks.pop(jt.group_id, None)
                if jt.pending_seq is not None:
                    # Messages arrived while judging — run their own round.
                    self._schedule_judge(jt.group_id, jt.pending_seq, jt.pending_msg)

    async def _invoke_judge(self, group_id: str, latest_user, msg: dict) -> Optional[Dict[str, Any]]:
        try:
            from .semantic_judge import pre_reply_judge as _sj
        except ImportError:
            return None

        gs = self._adapter._group_states.get(group_id)
        recent_raw = gs.get_recent()

        is_mention = msg.get("_is_mentioned", False)

        recent_no_current = [m for m in recent_raw if m.seq < latest_user.seq]
        recent_dicts = [
            {"ts_str": time.strftime('%m-%d %H:%M', time.localtime(m.ts)),
             "name": m.name, "text": m.text[:200], "is_bot": m.is_bot,
             "is_at": m.at_self, "at_targets": list(getattr(m, "at_targets", []) or [])}
            for m in recent_no_current[-10:]
        ]

        follow_up_dicts = []
        if is_mention:
            follow_up_dicts = [
                {"ts_str": time.strftime('%m-%d %H:%M', time.localtime(m.ts)),
                 "name": m.name, "text": m.text[:200], "is_bot": m.is_bot,
                 "is_at": m.at_self, "at_targets": list(getattr(m, "at_targets", []) or [])}
                for m in recent_raw if m.seq > latest_user.seq
            ][-5:]

        raw_text = self._adapter._cq_to_readable(self._adapter._get_raw_text(msg) or "")

        from .semantic_judge import _get_bot_name as _get_bn
        _self_id_str = str(self._adapter._self_id) if self._adapter._self_id else ""
        _bot_name = _get_bn()
        if _self_id_str and raw_text:
            import re
            raw_text = re.sub(r'@QQ' + re.escape(_self_id_str) + r'(?!\d)', f'@{_bot_name}', raw_text)

        # Runtime @-targets (names from adapter, never hardcoded) — keep
        # verbatim, else "@某人 玩去吧" reads as addressed to us.
        _at_targets = list(getattr(latest_user, "at_targets", []) or [])
        if _at_targets and not is_mention:
            _target_label = "、".join(_at_targets)
            raw_text = raw_text + f" [@:{_target_label}]"

        msg_type_str = "text"
        if self._adapter._has_image_message(msg):
            msg_type_str = "image"
        elif self._adapter._has_voice_message(msg):
            msg_type_str = "voice"

        current_dict = {
            "ts_str": time.strftime('%m-%d %H:%M', time.localtime(msg.get("time", 0) or time.time())),
            "name": latest_user.name, "text": raw_text[:300],
            "msg_type": msg_type_str, "is_at": is_mention,
            "at_targets": _at_targets,
        }
        if follow_up_dicts:
            current_dict["follow_up"] = follow_up_dicts

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
                bot_name=_bot_name,
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

    # ── submit to executor ──────────────────────────────────

    def _submit_request(self, request: TriggerRequest):
        self._pending_requests[request.group_id] = request
        self._adapter._schedule_group_run(request)
