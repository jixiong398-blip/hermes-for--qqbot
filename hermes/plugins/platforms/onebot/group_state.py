import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class BufferedMessage:
    mid: str
    ts: float
    uid: str
    name: str
    text: str
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

    def reset(self, reply_text: str = ""):
        self.active = True
        self.silent_count = 0
        self.last_active_ts = time.time()
        self.last_reply = reply_text

    def deactivate(self):
        self.active = False


class GroupState:
    WINDOW_SECONDS = 300

    def __init__(self, group_id: str):
        self.group_id = group_id
        self.buffer: List[BufferedMessage] = []
        self.last_reply: Optional[Tuple[float, str]] = None
        self.attentive = AttentiveState()
        self.episode_start: float = 0.0
        self.reply_count: int = 0
        self.episode_archived: bool = False
        self.rolling_summary: str = ""
        self.last_agent_ts: float = 0.0

    def append_message(self, msg: BufferedMessage):
        self.buffer.append(msg)
        if self.episode_start == 0.0:
            self.episode_start = msg.ts

    def get_recent(self, window_seconds: Optional[float] = None) -> List[BufferedMessage]:
        if window_seconds is None:
            window_seconds = self.WINDOW_SECONDS
        cutoff = time.time() - window_seconds
        return [m for m in self.buffer if m.ts >= cutoff]

    def expire_window(self):
        cutoff = time.time() - self.WINDOW_SECONDS
        self.buffer = [m for m in self.buffer if m.ts >= cutoff]

    def enter_attentive(self, reply_text: str = ""):
        self.attentive.reset(reply_text)

    def is_attentive(self) -> bool:
        if not self.attentive.active:
            return False
        if time.time() - self.attentive.last_active_ts > 600:
            self.attentive.active = False
            return False
        if self.attentive.silent_count >= 3:
            self.attentive.active = False
            return False
        return True

    def record_silent(self):
        if self.attentive.active:
            self.attentive.silent_count += 1

    def record_reply(self, reply_text: str = ""):
        self.attentive.reset(reply_text)
        self.last_reply = (time.time(), reply_text)
        self.reply_count += 1
        self.episode_archived = False

    def go_quiet(self):
        self.attentive.deactivate()
        self.last_reply = None

    def end_episode(self):
        self.attentive.deactivate()
        self.last_reply = None
        self.episode_archived = True
        self.reply_count = 0
        self.episode_start = 0.0

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
