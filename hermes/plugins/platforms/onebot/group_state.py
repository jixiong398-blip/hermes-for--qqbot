import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

BUFFER_MAX_SIZE = 200
BUFFER_PRUNE_EVERY = 50


@dataclass
class BufferedMessage:
    seq: int = 0
    mid: str = ""
    ts: float = 0.0
    uid: str = ""
    name: str = ""
    text: str = ""
    is_bot: bool = False
    msg_type: str = "text"
    media_paths: List[str] = field(default_factory=list)
    descriptions: List[str] = field(default_factory=list)


@dataclass
class AttentiveState:
    active: bool = False
    silent_count: int = 0
    last_active_ts: float = 0.0
    last_reply: str = ""

    def activate(self):
        self.active = True

    def refresh_on_reply(self, reply_text: str = ""):
        self.active = True
        self.silent_count = 0
        self.last_active_ts = time.time()
        self.last_reply = reply_text

    def deactivate(self):
        self.active = False


@dataclass
class EpisodeState:
    """结构化对话状态，跨轮传递 — 对标 Kazusa conversation_progress。

    每轮对话后由 Post-reply Recorder 更新，下一轮 Pre-reply Judge 消费。
    """

    # ── 生命周期 ──
    status: str = "active"           # active | winding_down | closed
    continuity: str = "same_episode" # same_episode | related_shift | sharp_transition
    turn_count: int = 0

    # ── 话题状态 ──
    episode_label: str = ""
    current_thread: str = ""
    conversation_mode: str = ""      # casual_chat | tech_discussion | playful_banter | group_ambient | serious
    episode_phase: str = ""          # starting | mid | winding_down | exiting

    # ── 说话人角色 ──
    last_speaker_role: str = ""      # owner | member | bot | unknown

    # ── Bot 行为追踪 ──
    bot_moves: List[str] = field(default_factory=list)
    overused_moves: List[str] = field(default_factory=list)
    open_loops: List[str] = field(default_factory=list)
    resolved_threads: List[str] = field(default_factory=list)

    # ── 下一轮指导 ──
    progression_guidance: str = ""

    # ── 元数据 ──
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> Dict:
        """序列化为 judge/recorder 可消费的纯 dict。list 字段转为 list[str]。"""
        return {
            "status": self.status,
            "continuity": self.continuity,
            "turn_count": self.turn_count,
            "episode_label": self.episode_label,
            "current_thread": self.current_thread,
            "conversation_mode": self.conversation_mode,
            "episode_phase": self.episode_phase,
            "last_speaker_role": self.last_speaker_role,
            "bot_moves": list(self.bot_moves),
            "overused_moves": list(self.overused_moves),
            "open_loops": list(self.open_loops),
            "resolved_threads": list(self.resolved_threads),
            "progression_guidance": self.progression_guidance,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "EpisodeState":
        """从 recorder/judge 输出重建。容忍缺失键 + 字段名变体。"""
        return cls(
            status=str(d.get("status", d.get("episode_status", "active"))),
            continuity=str(d.get("continuity", "same_episode")),
            turn_count=int(d.get("turn_count", 0)),
            episode_label=str(d.get("episode_label", "")),
            current_thread=str(d.get("current_thread", "")),
            conversation_mode=str(d.get("conversation_mode", "")),
            episode_phase=str(d.get("episode_phase", "")),
            last_speaker_role=str(d.get("last_speaker_role", d.get("speaker_role", ""))),
            bot_moves=list(d.get("bot_moves", d.get("soyo_moves", []))),
            overused_moves=list(d.get("overused_moves", [])),
            open_loops=list(d.get("open_loops", [])),
            resolved_threads=list(d.get("resolved_threads", [])),
            progression_guidance=str(d.get("progression_guidance", "")),
            created_at=float(d.get("created_at", 0.0)),
            updated_at=float(d.get("updated_at", 0.0)),
        )

    @classmethod
    def empty(cls) -> "EpisodeState":
        return cls()


class GroupState:
    WINDOW_SECONDS = 300

    def __init__(self, group_id: str):
        self.group_id = group_id
        self.buffer: List[BufferedMessage] = []
        self.by_mid: Dict[str, BufferedMessage] = {}

        self.next_seq: int = 0
        self.last_user_seq: int = 0
        self.last_consumed_seq: int = 0
        self.last_judged_seq: int = 0
        self.decision_epoch: int = 0

        self.last_reply: Optional[Tuple[float, str]] = None
        self.attentive = AttentiveState()
        self.episode_start: float = 0.0
        self.reply_count: int = 0
        self.episode_archived: bool = False
        self.rolling_summary: str = ""
        self.last_agent_ts: float = 0.0
        self.episode_state: EpisodeState = EpisodeState.empty()

    # ── ingestion ──────────────────────────────────────────────

    def append_message(self, msg: BufferedMessage) -> BufferedMessage:
        self.next_seq += 1
        msg.seq = self.next_seq
        self.buffer.append(msg)
        if not msg.is_bot:
            self.last_user_seq = msg.seq
        if msg.mid:
            self.by_mid[msg.mid] = msg
        if self.episode_start == 0.0:
            self.episode_start = msg.ts
        if len(self.buffer) > BUFFER_MAX_SIZE + BUFFER_PRUNE_EVERY:
            self.prune()
        return msg

    # ── lookup ─────────────────────────────────────────────────

    def lookup_mid(self, mid: str) -> Optional[BufferedMessage]:
        return self.by_mid.get(mid)

    # ── snapshot ───────────────────────────────────────────────

    def snapshot(self, up_to_seq: Optional[int] = None) -> Tuple[BufferedMessage, ...]:
        """Immutable tuple of messages up to (and including) up_to_seq."""
        if up_to_seq is None:
            up_to_seq = self.last_user_seq
        return tuple(m for m in self.buffer if m.seq <= up_to_seq)

    def snapshot_meta(self) -> Dict:
        return {
            "attentive": self.is_attentive(),
            "episode_active": self.is_episode_active(),
            "reply_count": self.reply_count,
            "rolling_summary": self.rolling_summary,
            "decision_epoch": self.decision_epoch,
        }

    # ── user messages after watermark ──────────────────────────

    def user_messages_after(self, seq: int) -> List[BufferedMessage]:
        return [m for m in self.buffer if m.seq > seq and not m.is_bot]

    def latest_user_message(self) -> Optional[BufferedMessage]:
        for m in reversed(self.buffer):
            if not m.is_bot:
                return m
        return None

    # ── watermark ──────────────────────────────────────────────

    def mark_consumed(self, seq: int):
        if seq > self.last_consumed_seq:
            self.last_consumed_seq = seq

    def mark_judged(self, seq: int):
        if seq > self.last_judged_seq:
            self.last_judged_seq = seq

    def increment_decision_epoch(self):
        self.decision_epoch += 1

    # ── get_recent (preserve existing API) ─────────────────────

    def get_recent(self, window_seconds: Optional[float] = None) -> List[BufferedMessage]:
        if window_seconds is None:
            window_seconds = self.WINDOW_SECONDS
        cutoff = time.time() - window_seconds
        return [m for m in self.buffer if m.ts >= cutoff]

    # ── prune ──────────────────────────────────────────────────

    def prune(self):
        if len(self.buffer) <= BUFFER_MAX_SIZE:
            return
        cutoff_seq = self.buffer[-BUFFER_MAX_SIZE].seq
        removed = self.buffer[:-BUFFER_MAX_SIZE]
        self.buffer = self.buffer[-BUFFER_MAX_SIZE:]
        for m in removed:
            if m.mid and m.mid in self.by_mid:
                del self.by_mid[m.mid]

    # ── attentive state ────────────────────────────────────────

    def is_attentive(self) -> bool:
        if not self.attentive.active:
            return False
        if time.time() - self.attentive.last_active_ts > 600:
            self.attentive.deactivate()
            return False
        if self.attentive.silent_count >= 3:
            self.attentive.deactivate()
            return False
        return True

    def enter_attentive(self, reply_text: str = ""):
        """Enter attentive mode. Does NOT reset silent_count if already active."""
        if not self.attentive.active:
            self.attentive.activate()
            self.attentive.silent_count = 0
            self.attentive.last_reply = reply_text
        self.attentive.last_active_ts = time.time()
        if reply_text:
            self.attentive.last_reply = reply_text

    def record_reply(self, reply_text: str = ""):
        """Actual reply sent. Refresh all attentive timers and clear silent count."""
        self.attentive.refresh_on_reply(reply_text)
        self.last_reply = (time.time(), reply_text)
        self.reply_count += 1
        self.episode_archived = False

    def record_silent(self):
        if self.attentive.active:
            self.attentive.silent_count += 1
            if self.attentive.silent_count >= 3:
                self.attentive.deactivate()

    def go_quiet(self):
        """Bot被赶/主动离开：退出对话态，但保留 episode_state。
        
        与 end_episode 的区别：不清空 episode_state，下次被 @ 回来时可继续。
        """
        self.attentive.deactivate()
        self.last_reply = None

    def end_episode(self):
        """话题彻底结束：退出对话态 + 重置信标 + 清空 episode_state。"""
        self.attentive.deactivate()
        self.last_reply = None
        self.episode_archived = True
        self.reply_count = 0
        self.episode_start = 0.0
        self.episode_state = EpisodeState.empty()

    def is_episode_active(self) -> bool:
        return not self.episode_archived and self.last_reply is not None

    def get_attentive_meta(self) -> Dict:
        if not self.is_attentive():
            return {}
        mins = int((time.time() - self.attentive.last_active_ts) / 60)
        return {
            "silent_count": self.attentive.silent_count,
            "last_reply": self.attentive.last_reply,
            "mins_since_active": mins,
            "reply_count": self.reply_count,
        }


class GroupStateRegistry:
    def __init__(self):
        self._states: Dict[str, GroupState] = {}

    def get(self, group_id: str) -> GroupState:
        if group_id not in self._states:
            self._states[group_id] = GroupState(group_id)
        return self._states[group_id]

    def get_all(self) -> Dict[str, GroupState]:
        return self._states

    def get_active_groups(self) -> List[str]:
        return [gid for gid, gs in self._states.items() if gs.is_episode_active()]
