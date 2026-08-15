#         ┌─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┐         
#          │      清   尘   璃   落      │          
#         └─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┘         
#              上联：代码永无 bug  佛祖座下莲花放             
#               下联：清尘璃落赐福  素世心中万世安              
#
#                    _ooOoo_                    
#                   o8888888o                   
#                   88" . "88                   
#                   (| -_- |)                   
#                   O\  =  /O                   
#                ____/`---'\____                
#              .'  \\|     |//  `.              
#             /  \\|||  :  |||//  \             
#            /  _||||| -:- |||||-  \            
#            |   | \\\  -  /// |   |            
#            | \_|  ''\---/''  |   |            
#            \  .-\__  `-`  ___/-. /            
#           ___`. .'  /--.--\  `. . __          
#        ."" '<  `.___\_<|>_/___.'  >'"".       
#      | | :  `- \`.;`\ _ /`;.`/ - ` : | |      
#      \  \ `-.   \_ __\ /__ _/   .-` /  /      
# ======`-.____`-.___\_____/___.-`____.-'====== 
#                    `=---='                    
#          }  }  }  }  莲花台  {  {  {  {          
#          }  }  }  }  莲花台  {  {  {  {          
#          }  }  }  }  莲花台  {  {  {  {          
# ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#                 佛祖保佑    永无 bug                
#

"""OneBot v11 adapter for Hermes Agent.

Connects to QQ via NapCat/Lagrange/go-cqhttp using the OneBot v11 protocol
over WebSocket. Supports both forward (bot connects to NapCat) and backward
(NapCat connects to bot) modes.

Configuration in config.yaml:
    platforms:
      onebot:
        enabled: true
        extra:
          ws_url: "ws://127.0.0.1:3001/onebot/v11/ws"
          access_token: ""             # optional
          require_mention: false       # group messages must @-mention bot
          allowed_users: []            # whitelist (empty = all)
          blocked_users: []            # blacklist

Or via environment variables:
    ONEBOT_WS_URL=ws://127.0.0.1:3001/onebot/v11/ws
    ONEBOT_ACCESS_TOKEN=
    ONEBOT_ALLOWED_USERS=123456,789012
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
import subprocess
import sys as _sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from hermes_constants import get_state_db_path

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)
from gateway.session import SessionSource, build_session_key

logger = logging.getLogger(__name__)


# ── QQ system face emoji (type=face) id → name map ──────────────────────────
# Loaded once at import time from qq_face_map.json (sibling file).
# NapCat's face_config.json exposes ~282 entries; we ship a curated subset
# so [CQ:face,id=177] becomes [喷血] in context instead of [动画表情].
# Per OneBot v11 spec, face is a SYSTEM emoji (inline text glyph), NOT an
# image — it has no url/file/summary, only id. Treating it as an image
# triggers a futile vision API call that returns "图片", poisoning context.
def _load_qq_face_map() -> Dict[str, str]:
    _path = Path(__file__).parent / "qq_face_map.json"
    if not _path.exists():
        return {}
    try:
        with _path.open("r", encoding="utf-8") as _f:
            data = json.load(_f)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except Exception as _e:
        logger.warning("[OneBot] failed to load qq_face_map.json: %s", _e)
    return {}


QQ_FACE_MAP: Dict[str, str] = _load_qq_face_map()


def _qq_face_text(face_id: str) -> str:
    """Return human-readable name for a QQ face emoji id, e.g. '177' → '[喷血]'.

    Falls back to '[QQ表情:ID]' if the id is not in the map (new emoji added
    by Tencent but the local face_config.json hasn't been refreshed).
    """
    name = QQ_FACE_MAP.get(str(face_id), "")
    if name:
        return f"[{name}]"
    return f"[QQ表情:{face_id}]"


# ── Agent-curated sticker library ────────────────────────────────────────────
# Soyo can save images she sees in group chats as her own stickers, tagged by
# emotion (uses tools/sticker_curator_tool.py). This helper resolves an
# emotion name from `[sticker:<emotion>]` to the most recently collected image
# under that emotion. Returns "" if no match, letting the caller fall back to
# built-in chibi stickers.
_STICKER_COLLECTION_INDEX = Path(os.getenv(
    "SOYO_STICKER_INDEX",
    os.path.join(os.getenv("HERMES_HOME", os.path.expanduser("~/.hermes")),
                 "soyo_sticker_collection.json"),
))

# ── Forward-transcript budget ────────────────────────────────────────────────
# Forwarded chat transcripts may contain text/images/videos/links stacked
# together. The main model has a 1M-token context, so we keep a generous
# budget and LLM-compress instead of hard-truncating when exceeded.
_MAX_FORWARD_DETAIL_CHARS = 500000


def _lookup_collected_sticker(emotion: str) -> str:
    """Return path to most recently collected sticker tagged `emotion`, or "".

    Index file is re-read on each call rather than cached at import time —
    curate/remove writes happen at runtime and the adapter must observe
    them without restart. File is small (≤200 entries total) and _sticker_path
    is only called when Soyo actively emits a sticker label, not on every
    message, so the IO cost is acceptable.

    All read errors are logged at WARNING so a corrupted index doesn't
    silently drop every collected sticker — visible in gateway logs.
    """
    if not emotion:
        return ""
    try:
        with _STICKER_COLLECTION_INDEX.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return ""  # never curated — expected, no log
    except (OSError, ValueError) as e:
        logger.warning("[OneBot] collected sticker index unreadable (%s); falling back to built-in", e)
        return ""
    if not isinstance(data, dict) or not isinstance(data.get("emotions"), dict):
        logger.warning("[OneBot] collected sticker index has wrong structure (got %s, expected dict with 'emotions')",
                       type(data).__name__ if not isinstance(data, dict) else "dict without 'emotions' field")
        return ""
    emap = data["emotions"]
    entries = emap.get(emotion) or emap.get(emotion.lower())
    if not entries:
        return ""
    # Most recent last; curate appends so [-1] is newest.
    path = entries[-1].get("path", "")
    return path if path and Path(path).is_file() else ""


class OneBotAdapter(BasePlatformAdapter):
    """OneBot v11 adapter for QQ (NapCat/Lagrange/go-cqhttp)."""

    # QQ does not support message editing, so streaming (which relies on edits)
    # must be disabled. The gateway will fall back to sending the full response
    # as a single message.
    SUPPORTS_MESSAGE_EDITING = False

    # QQ/NapCat is a chat platform — system progress messages (⚡ busy-ack,
    # ⏳ draining, 💾 self-review, etc.) must NOT be sent to the user.
    # The gateway checks this flag before sending any system-initiated message.
    SUPPORTS_SYSTEM_MESSAGES = False

    def __init__(self, config, **kwargs):
        from gateway.config import Platform as _Platform
        super().__init__(config=config, platform=_Platform("onebot"))
        self._ws = None
        self._ws_task: Optional[asyncio.Task] = None
        self._http_client = None
        self._echo_counter = 0
        self._pending_echo: Dict[str, asyncio.Future] = {}
        _preload_self_id = os.getenv("ONEBOT_SELF_ID", "").strip()
        self._self_id: Optional[int] = int(_preload_self_id) if _preload_self_id.isdigit() else None
        self._bot_name: str = os.getenv("ONEBOT_BOT_NAME", "").strip() or "Soyo"
        self._ws_url: str = ""
        self._http_url: str = ""
        self._access_token: str = ""
        self._reverse_ws: bool = False     # gateway listens, NapCat connects
        self._reverse_ws_port: int = 3002   # port to listen on in reverse mode
        self._ws_server = None             # websockets server instance
        self._require_mention: bool = False
        self._allowed_users: set = set()
        self._blocked_users: set = set()

        # Image+text debouncing: wait for rapid follow-up text after image
        self._image_text_delay_seconds = float(os.getenv("HERMES_ONEBOT_IMAGE_TEXT_DELAY_SECONDS", "2.5"))
        self._pending_image_events: Dict[str, MessageEvent] = {}
        self._pending_image_tasks: Dict[str, asyncio.Task] = {}
        # Multi-@mention batching: merge nearby @mentions for one agent run
        self._pending_mentions: Dict[str, list] = {}  # group_id → [{name, text, ts}]
        self._mention_flush_tasks: Dict[str, asyncio.Task] = {}
        self._mention_batch_delay = 3.0  # seconds to wait for more @mentions

        from .group_state import GroupStateRegistry
        self._group_states = GroupStateRegistry()
        self._group_send_results: Dict[str, "asyncio.Future"] = {}

        # Image description cache: avoid re-calling vision API for same image
        self._image_descriptions: Dict[str, str] = {}
        self._voice_transcripts: Dict[str, str] = {}

        self._last_bot_reply: Dict[str, tuple] = {}

        # Async persist queue: decouple SQLite writes from message processing
        self._persist_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._persist_worker_task: Optional[asyncio.Task] = None

        # Per-group lock: ensure messages from the same group are processed serially
        # Prevents concurrent agent runs from clobbering each other's context
        self._group_locks: Dict[str, asyncio.Lock] = {}
        self._dm_locks: Dict[str, asyncio.Lock] = {}

        # Phase 2/3 modules — orchestrated from _process_message_impl
        from .media_pipeline import MediaPipeline
        from .trigger_coordinator import TriggerCoordinator
        from .group_executor import GroupExecutor
        self._media_pipeline = MediaPipeline(self)
        self._trigger_coordinator = TriggerCoordinator(self)
        self._group_executor = GroupExecutor(self)

        # WebSocket concurrent ingestion
        self._ingress_semaphore = asyncio.Semaphore(20)
        self._inflight_tasks: set = set()

        # Message dedup: prevent processing the same message twice
        self._seen_msg_ids: Dict[str, float] = {}  # msg_id → seen_at timestamp
        self._seen_forward_ids: Dict[str, float] = {}  # forward_id → seen_at timestamp
        self._DEDUP_TTL = 30  # 30 seconds — only guards against reconnect replay

        # Reconnect tuning: independent of gateway's global backoff
        self._ws_reconnect_interval: int = int(os.getenv("ONEBOT_RECONNECT_INTERVAL", "10"))

    def add_bot_reply_to_buffer(self, chat_id: str, text: str, is_voice: bool = False):
        if chat_id.startswith("group:"):
            group_id = chat_id.split(":", 1)[1]
            label = "[语音]" if is_voice else ""
            self._persist_chat_message(group_id, "group", 0, "bot", text, is_bot=1)
            self._last_bot_reply[group_id] = (time.time(), text)
            try:
                from .group_state import BufferedMessage
                _gs = self._group_states.get(group_id)
                _gs.append_message(
                    BufferedMessage(mid="", ts=time.time(), uid="0", name="bot",
                                    text=f"{label}{text}", is_bot=True)
                )
                _gs.record_reply(text)
            except Exception:
                pass
            logger.info("[OneBot] add_bot_reply_to_buffer: group=%s text=%d chars", group_id, len(text))
        else:
            self._persist_chat_message(chat_id, "private", 0, "bot", text, is_bot=1)

    def _schedule_group_run(self, request):
        self._group_executor.schedule(request)

    def get_group_buffer_snapshot(self, group_id: str, window_seconds: int = 300) -> list:
        gs = self._group_states.get(group_id)
        msgs = gs.get_recent(window_seconds=window_seconds)
        return [{"name": m.name, "text": m.text, "ts": m.ts, "uid": m.uid, "mid": m.mid}
                for m in msgs]

    def _persist_chat_message(self, group_id: str, chat_type: str, user_id: int,
                               sender_name: str, content: str, message_id: str = "",
                               created_at: float = None, is_bot: int = 0,
                               *, content_raw: str = "", sender_card: str = "",
                               group_name: str = "", image_descriptions: list = None,
                               reply_to_id: str = "", reply_to_text: str = "",
                               at_targets: list = None):
        try:
            self._persist_queue.put_nowait((group_id, chat_type, user_id, sender_name,
                                            content, message_id, created_at, is_bot,
                                            content_raw, sender_card, group_name,
                                            image_descriptions or [],
                                            reply_to_id, reply_to_text,
                                            at_targets or []))
        except asyncio.QueueFull:
            logger.warning("[OneBot] Persist queue full, dropping message from %s", sender_name)

    async def _persist_worker(self):
        """Background worker: drain persist queue → write to SQLite with retry.
        Dual-write: chat_message_buffer (runtime, prunable) + corpus_messages (permanent, training)."""
        import sqlite3, time as _time, json as _json
        db_path = str(get_state_db_path())
        _corpus_inited = False
        while True:
            try:
                (group_id, chat_type, user_id, sender_name, content, message_id,
                 created_at, is_bot, content_raw, sender_card, group_name,
                 image_descs, reply_to_id, reply_to_text, at_targets) = await self._persist_queue.get()
                cid = str(user_id) if chat_type == "private" else group_id
                _ts = created_at if created_at is not None else _time.time()
                for attempt in range(3):
                    try:
                        db = sqlite3.connect(db_path, timeout=10)
                        db.execute("PRAGMA journal_mode=WAL")
                        db.execute("PRAGMA synchronous=NORMAL")
                        db.execute("PRAGMA busy_timeout=30000")
                        if not _corpus_inited:
                            db.executescript("""
                                CREATE TABLE IF NOT EXISTS corpus_messages (
                                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                                    message_id TEXT DEFAULT '',
                                    chat_id TEXT NOT NULL,
                                    chat_type TEXT NOT NULL DEFAULT 'group',
                                    group_id TEXT DEFAULT '',
                                    user_id INTEGER,
                                    sender_name TEXT NOT NULL DEFAULT '',
                                    sender_card TEXT DEFAULT '',
                                    content_raw TEXT DEFAULT '',
                                    content_readable TEXT NOT NULL DEFAULT '',
                                    image_descriptions TEXT DEFAULT '[]',
                                    voice_transcript TEXT DEFAULT '',
                                    video_understanding TEXT DEFAULT '',
                                    forward_structured TEXT DEFAULT '[]',
                                    at_targets TEXT DEFAULT '[]',
                                    reply_to_id TEXT DEFAULT '',
                                    reply_to_text TEXT DEFAULT '',
                                    is_bot INTEGER DEFAULT 0,
                                    media_paths TEXT DEFAULT '[]',
                                    media_cached INTEGER DEFAULT 0,
                                    created_at REAL NOT NULL,
                                    session_id TEXT DEFAULT '',
                                    recalled_mem_ids TEXT DEFAULT '[]',
                                    salience_hint REAL DEFAULT 0.5
                                );
                                CREATE INDEX IF NOT EXISTS idx_corpus_chat_time ON corpus_messages(chat_id, created_at);
                                CREATE INDEX IF NOT EXISTS idx_corpus_group ON corpus_messages(group_id, created_at);
                                CREATE INDEX IF NOT EXISTS idx_corpus_message_id ON corpus_messages(message_id);
                                -- FTS5 full-text search for corpus_messages (trigram tokenizer for Chinese substring matching)
                                CREATE VIRTUAL TABLE IF NOT EXISTS corpus_messages_fts USING fts5(
                                    content_readable, sender_name,
                                    content='corpus_messages',
                                    content_rowid='id',
                                    tokenize='trigram'
                                );
                                -- INSERT trigger: corpus_messages is append-only (no UPDATE/DELETE triggers needed)
                                CREATE TRIGGER IF NOT EXISTS corpus_fts_ai AFTER INSERT ON corpus_messages BEGIN
                                    INSERT INTO corpus_messages_fts(rowid, content_readable, sender_name)
                                    VALUES (new.id, new.content_readable, new.sender_name);
                                END;
                                CREATE TABLE IF NOT EXISTS corpus_pairs (
                                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                                    trigger_msg_id TEXT DEFAULT '',
                                    bot_reply_text TEXT NOT NULL,
                                    context_snapshot TEXT DEFAULT '',
                                    system_prompt TEXT DEFAULT '',
                                    recalled_mem_ids TEXT DEFAULT '[]',
                                    session_id TEXT DEFAULT '',
                                    model_used TEXT DEFAULT '',
                                    was_corrected INTEGER DEFAULT 0,
                                    created_at REAL NOT NULL
                                );
                                CREATE INDEX IF NOT EXISTS idx_pairs_corrected ON corpus_pairs(was_corrected);
                                CREATE INDEX IF NOT EXISTS idx_pairs_session ON corpus_pairs(session_id);
                                CREATE TABLE IF NOT EXISTS groups_registry (
                                    group_id TEXT PRIMARY KEY,
                                    group_name TEXT DEFAULT '',
                                    joined_at REAL NOT NULL,
                                    is_active INTEGER DEFAULT 1,
                                    left_at REAL,
                                    activity_level REAL DEFAULT 0.5,
                                    wake_sensitivity REAL DEFAULT 1.0,
                                    notes TEXT DEFAULT '',
                                    topic_summary TEXT DEFAULT '',
                                    topic_keywords TEXT DEFAULT '[]',
                                    topic_updated_at REAL DEFAULT 0
                                );
                            """)
                            db.executescript("""
                                CREATE TABLE IF NOT EXISTS chat_message_buffer (
                                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                                    chat_id TEXT NOT NULL,
                                    chat_type TEXT NOT NULL DEFAULT 'group',
                                    user_id INTEGER,
                                    sender_name TEXT NOT NULL DEFAULT '',
                                    content TEXT NOT NULL,
                                    is_bot INTEGER DEFAULT 0,
                                    created_at REAL NOT NULL,
                                    message_id TEXT DEFAULT ''
                                );
                                CREATE INDEX IF NOT EXISTS idx_cmb_chat_time ON chat_message_buffer(chat_id, created_at);
                                CREATE TABLE IF NOT EXISTS qzone_posts (
                                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                                    content TEXT NOT NULL,
                                    images TEXT DEFAULT '[]',
                                    created_at REAL NOT NULL,
                                    posted_at REAL,
                                    status TEXT DEFAULT 'draft'
                                );
                                CREATE INDEX IF NOT EXISTS idx_qzone_status ON qzone_posts(status);
                            """)
                            _corpus_inited = True
                            try:
                                existing_cols = {r[1] for r in db.execute("PRAGMA table_info(groups_registry)")}
                                for _col, _def in [("topic_summary", "TEXT DEFAULT ''"),
                                                   ("topic_keywords", "TEXT DEFAULT '[]'"),
                                                   ("topic_updated_at", "REAL DEFAULT 0")]:
                                    if _col not in existing_cols:
                                        db.execute(f"ALTER TABLE groups_registry ADD COLUMN {_col} {_def}")
                            except Exception:
                                pass
                        db.execute(
                            "INSERT INTO chat_message_buffer (chat_id, chat_type, user_id, sender_name, content, message_id, created_at, is_bot) VALUES (?,?,?,?,?,?,?,?)",
                            (cid, chat_type, str(user_id), sender_name, content, str(message_id), _ts, is_bot),
                        )
                        db.execute(
                            """INSERT INTO corpus_messages
                               (message_id, chat_id, chat_type, group_id, user_id,
                                sender_name, sender_card, content_raw, content_readable,
                                image_descriptions, at_targets, reply_to_id, reply_to_text,
                                is_bot, created_at)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (str(message_id), cid, chat_type,
                             group_id if chat_type == "group" else "",
                             user_id, sender_name, sender_card,
                             content_raw, content,
                             _json.dumps(image_descs, ensure_ascii=False) if image_descs else "[]",
                             _json.dumps(at_targets, ensure_ascii=False) if at_targets else "[]",
                             reply_to_id, reply_to_text,
                             is_bot, _ts),
                        )
                        if chat_type == "group" and group_id:
                            db.execute(
                                "INSERT OR IGNORE INTO groups_registry (group_id, group_name, joined_at) VALUES (?, ?, ?)",
                                (group_id, group_name, _ts),
                            )
                            if group_name:
                                db.execute(
                                    "UPDATE groups_registry SET group_name=? WHERE group_id=? AND group_name!=?",
                                    (group_name, group_id, group_name),
                                )
                        if is_bot == 1:
                            db.execute(
                                """INSERT INTO corpus_pairs (bot_reply_text, created_at)
                                   VALUES (?, ?)""",
                                (content[:4000], _ts),
                            )
                        db.commit()
                        db.close()
                        break
                    except sqlite3.OperationalError as _dbe:
                        logger.error("[OneBot] Persist worker DB error (attempt %d/%d): %s | msg_id=%s sender=%s chat=%s",
                                     attempt + 1, 3, _dbe, message_id, sender_name, cid)
                        if attempt < 2:
                            await asyncio.sleep(1)
                        try:
                            db.close()
                        except Exception:
                            pass
                    except sqlite3.IntegrityError as _ie:
                        logger.error("[OneBot] Persist worker integrity error (attempt %d/%d): %s | msg_id=%s",
                                     attempt + 1, 3, _ie, message_id)
                        try:
                            db.close()
                        except Exception:
                            pass
                        break  # Integrity error won't fix with retry, skip this message
                    except Exception as _ue:
                        logger.error("[OneBot] Persist worker unexpected error (attempt %d/%d): %s | msg_id=%s sender=%s",
                                     attempt + 1, 3, _ue, message_id, sender_name, exc_info=True)
                        if attempt < 2:
                            await asyncio.sleep(1)
                        try:
                            db.close()
                        except Exception:
                            pass
                else:
                    logger.critical("[OneBot] Persist worker gave up after 3 attempts | msg_id=%s sender=%s chat=%s content_len=%d",
                                    message_id, sender_name, cid, len(content or ""))
                self._persist_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as _pe:
                logger.error("[OneBot] Persist worker outer error: %s", _pe, exc_info=True)
                await asyncio.sleep(1)

    @property
    def name(self) -> str:
        return "OneBot (QQ)"

    def _recall_context(self, message_text: str, sender_id: str,
                        sender_name: str, session_id: str = "") -> str:
        try:
            from agent.memory.gateway import UnifiedMemoryGateway
            gw = UnifiedMemoryGateway.get_instance()
            if not gw or not message_text or len(message_text.strip()) < 3:
                return ""

            structured = gw.recall_structured(message_text, session_id=session_id, max_chars=5000)

            parts = []
            if structured.get("prompt"):
                parts.append(structured["prompt"])

            sender_matches = gw._ltm.search(sender_id, limit=5) if sender_id else []
            if not sender_matches and sender_name:
                sender_matches = gw._ltm.search(sender_name, limit=5)
            if not sender_matches and sender_id:
                try:
                    from agent.memory.store import MemoryStore
                    sender_matches = MemoryStore().search_by_user_id(sender_id, limit=5)
                except Exception:
                    pass
            if sender_matches:
                profile_lines = []
                seen = set()
                for r in sender_matches:
                    if r.value and r.value.strip() and r.value not in seen:
                        seen.add(r.value)
                        profile_lines.append(r.value.strip()[:100])
                if profile_lines:
                    parts.append(f"[关于 {sender_name}] " + "；".join(profile_lines[:5]))

            return "\n\n".join(parts) if parts else ""
        except Exception as e:
            logger.warning("[OneBot] _recall_context failed: %s", e, exc_info=True)
            return ""

    def _load_config(self) -> None:
        extra = self.config.extra if self.config else {}

        self._ws_url = os.getenv("ONEBOT_WS_URL", extra.get("ws_url", "ws://127.0.0.1:3001/onebot/v11/ws"))
        self._access_token = os.getenv("ONEBOT_ACCESS_TOKEN", extra.get("access_token", ""))
        # Reverse WebSocket: Gateway listens, NapCat connects to us
        _rp = extra.get("reverse_ws_port", 0)
        if not _rp:
            _rp = int(os.getenv("ONEBOT_REVERSE_WS_PORT", "0") or 0)
        self._reverse_ws_port = int(_rp) if _rp else 0
        self._reverse_ws = self._reverse_ws_port > 0
        self._ws_server = None

        # Reconnect interval from config
        _ri = extra.get("reconnect_interval", 0)
        if not _ri:
            _ri = int(os.getenv("ONEBOT_RECONNECT_INTERVAL", "10") or 10)
        self._ws_reconnect_interval = int(_ri)

        # Derive HTTP URL from WS URL (replace ws:// with http://, remove path)
        parsed = urlparse(self._ws_url)
        self._http_url = os.getenv(
            "ONEBOT_HTTP_URL",
            extra.get("http_url", f"http://{parsed.hostname}:{parsed.port}")
        )

        # require_mention: group messages need @mention
        rm = extra.get("require_mention")
        if rm is not None:
            self._require_mention = bool(rm)
        else:
            self._require_mention = os.getenv("ONEBOT_REQUIRE_MENTION", "false").lower() in ("true", "1", "yes")

        # Allowed users (whitelist)
        allowed_str = os.getenv("ONEBOT_ALLOWED_USERS", extra.get("allowed_users", ""))
        if isinstance(allowed_str, list):
            self._allowed_users = {str(u) for u in allowed_str}
        elif allowed_str:
            self._allowed_users = {u.strip() for u in str(allowed_str).split(",") if u.strip()}

        # Blocked users (blacklist)
        blocked_str = os.getenv("ONEBOT_BLOCKED_USERS", extra.get("blocked_users", ""))
        if isinstance(blocked_str, list):
            self._blocked_users = {str(u) for u in blocked_str}
        elif blocked_str:
            self._blocked_users = {u.strip() for u in str(blocked_str).split(",") if u.strip()}

        # Admin ID: config.yaml extra.admin_id takes precedence, env as fallback.
        # This is the only QQ number that MUST be pre-configured - it controls
        # who can issue / slash commands. All other QQ/group IDs are discovered
        # at runtime from NapCat events.
        self._admin_id = str(
            extra.get("admin_id", "")
            or os.getenv("ONEBOT_ADMIN_ID", "")
        )

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        if not WEBSOCKETS_AVAILABLE:
            logger.error("websockets package not installed. Run: pip install websockets")
            self._set_fatal_error("no_websockets", "websockets package not installed", retryable=False)
            return False

        self._load_config()
        logger.info("[OneBot] Connecting: reverse_ws=%s, WS %s, HTTP %s", 
                     self._reverse_ws, self._ws_url, self._http_url)

        # Reverse WebSocket mode: Gateway listens, NapCat connects to us
        if self._reverse_ws:
            return await self._connect_reverse_ws()

        # Forward mode: Gateway connects to NapCat (original behavior)
        return await self._connect_forward_ws()

    async def _connect_reverse_ws(self) -> bool:
        """Start a WebSocket server on reverse_ws_port. NapCat connects to us."""
        port = self._reverse_ws_port
        logger.info("[OneBot] Reverse WS: listening on ws://127.0.0.1:%s/onebot", port)

        async def handler(ws):
            """Handle one incoming NapCat connection."""
            logger.info("[OneBot] Reverse WS: NapCat connected")
            self._ws = ws
            self._mark_connected()
            # Init HTTP client
            if HTTPX_AVAILABLE:
                self._http_client = httpx.AsyncClient(
                    base_url=self._http_url,
                    timeout=httpx.Timeout(15.0),
                    headers={"Authorization": f"Bearer {self._access_token}"} if self._access_token else {},
                )
            # Recover missed messages
            asyncio.create_task(self._recover_missed_messages())
            # Start persist worker (reverse mode must also persist)
            if self._persist_worker_task is None:
                self._persist_worker_task = asyncio.create_task(self._persist_worker())
            # Enter message loop
            await self._ws_loop()
            logger.info("[OneBot] Reverse WS: NapCat disconnected")

        try:
            self._ws_server = await websockets.serve(
                handler, "127.0.0.1", port,
                ping_interval=20, ping_timeout=10,
            )
            logger.info("[OneBot] Reverse WS server started on port %s", port)
            return True
        except Exception as e:
            logger.error("[OneBot] Reverse WS server failed: %s", e)
            self._set_fatal_error("reverse_ws_failed", str(e), retryable=True)
            return False

    async def _connect_forward_ws(self) -> bool:
        """Connect to NapCat as a WebSocket client (original behavior)."""
        try:
            additional_headers = {}
            if self._access_token:
                additional_headers["Authorization"] = f"Bearer {self._access_token}"

            self._ws = await websockets.connect(
                self._ws_url,
                additional_headers=additional_headers if additional_headers else None,
                ping_interval=15,
                ping_timeout=30,
            )
            self._ws_task = asyncio.create_task(self._ws_loop())
            self._persist_worker_task = asyncio.create_task(self._persist_worker())

            if HTTPX_AVAILABLE:
                self._http_client = httpx.AsyncClient(
                    base_url=self._http_url,
                    timeout=httpx.Timeout(15.0),
                    headers={"Authorization": f"Bearer {self._access_token}"} if self._access_token else {},
                )
                logger.info("[OneBot] HTTP client initialized at %s", self._http_url)

            self._mark_connected()
            logger.info("[OneBot] Connected successfully")
            asyncio.create_task(self._recover_missed_messages())
            return True
        except Exception as e:
            logger.error("[OneBot] Connection failed: %s", e)
            self._set_fatal_error("connection_failed", str(e), retryable=True)
            return False

    async def disconnect(self) -> None:
        self._mark_disconnected()
        # Cancel media pipeline
        self._media_pipeline.cancel_all()
        # Cancel inflight ingress tasks
        inflight = list(self._inflight_tasks)
        for task in inflight:
            if not task.done():
                task.cancel()
        if inflight:
            await asyncio.gather(*inflight, return_exceptions=True)
        # Stop persist worker
        if self._persist_worker_task and not self._persist_worker_task.done():
            self._persist_worker_task.cancel()
            try:
                await self._persist_worker_task
            except (asyncio.CancelledError, Exception):
                pass
            self._persist_worker_task = None
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except (asyncio.CancelledError, Exception):
                pass
            self._ws_task = None
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._http_client:
            try:
                await self._http_client.aclose()
            except Exception:
                pass
            self._http_client = None

    # ------------------------------------------------------------------
    # WebSocket message loop
    # ------------------------------------------------------------------

    async def _ws_loop(self) -> None:
        """Main WebSocket event loop — runs as background task after connect()."""
        if not self._ws:
            return

        logger.info("[OneBot] WebSocket event loop started")

        try:
            async for raw in self._ws:
                logger.info("[OneBot] Raw message received, length=%d", len(raw))
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("[OneBot] Invalid JSON received")
                    continue

                logger.info("[OneBot] Received: post_type=%s, keys=%s", payload.get("post_type"), list(payload.keys()))

                if "echo" in payload:
                    self._handle_echo_response(payload)
                    continue

                if payload.get("post_type") == "meta_event" and payload.get("meta_event_type") == "heartbeat":
                    continue

                if payload.get("post_type") == "message":
                    msg_type = payload.get("message_type", "")
                    logger.info("[OneBot] Incoming message: type=%s, user_id=%s, group_id=%s, raw_message=%s",
                                msg_type, payload.get("user_id"), payload.get("group_id"), str(payload.get("raw_message", ""))[:100])
                    if msg_type in ("private", "group"):
                        task = asyncio.create_task(self._process_message_bounded(payload))
                        self._inflight_tasks.add(task)
                        task.add_done_callback(self._inflight_tasks.discard)

                elif payload.get("post_type") == "notice":
                    await self._process_notice(payload)

        except websockets.exceptions.ConnectionClosed:
            logger.warning("[OneBot] WebSocket connection closed")
            self._mark_disconnected()
            self._set_fatal_error("ws_disconnected", "WebSocket connection closed", retryable=True)
            asyncio.create_task(self._notify_fatal_error())
        except Exception as e:
            logger.error("[OneBot] Error in event loop: %s", e)
            self._mark_disconnected()
            self._set_fatal_error("ws_error", str(e), retryable=True)
            asyncio.create_task(self._notify_fatal_error())

    # ------------------------------------------------------------------
    # Echo request/response correlation
    # ------------------------------------------------------------------

    def _handle_echo_response(self, payload: dict) -> None:
        echo = payload.get("echo")
        if echo and echo in self._pending_echo:
            future = self._pending_echo.pop(echo)
            if not future.done():
                future.set_result(payload)

    async def _send_action(self, action: str, params: dict, timeout: float = 15.0) -> dict:
        """Send an API action via HTTP POST."""
        if not self._http_client:
            raise RuntimeError("OneBot HTTP client not initialized")

        try:
            response = await self._http_client.post(action, json=params)
            result = response.json()
            if result.get("retcode") != 0:
                logger.warning("[OneBot] Action %s failed: %s", action, result)
            return result
        except Exception as e:
            logger.error("[OneBot] HTTP action %s failed: %s", action, e)
            raise

    # ------------------------------------------------------------------
    # Message parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _get_raw_text(msg: dict) -> str:
        """Extract plain text from a OneBot message."""
        if msg.get("raw_message") is not None:
            return msg["raw_message"]
        return OneBotAdapter._get_text_from_segments(msg)

    @staticmethod
    def _get_text_from_segments(msg: dict) -> str:
        """Concatenate text segments from message array."""
        segments = msg.get("message", [])
        if not isinstance(segments, list):
            return ""
        return "".join(
            seg.get("data", {}).get("text", "")
            for seg in segments
            if seg.get("type") == "text"
        )

    @staticmethod
    def _is_mentioned(msg: dict, self_id: int) -> bool:
        """Check if the message @-mentions the bot."""
        segments = msg.get("message", [])
        self_str = str(self_id)
        if isinstance(segments, list):
            for seg in segments:
                if seg.get("type") == "at":
                    qq = seg.get("data", {}).get("qq") or seg.get("data", {}).get("id")
                    if str(qq) == self_str:
                        return True
            return False
        # CQ-code fallback: when message is a raw string (or missing), parse
        # the CQ at segment from raw_message so @ detection still works.
        raw = msg.get("raw_message", "") or ""
        for m in re.finditer(r"\[CQ:at,qq=(\d+)[^\]]*\]", raw):
            if m.group(1) == self_str:
                return True
        return False

    @staticmethod
    def _get_reply_message_id(msg: dict) -> Optional[int]:
        """Extract replied-to message ID from reply segment."""
        segments = msg.get("message", [])
        if not isinstance(segments, list):
            return None
        for seg in segments:
            if seg.get("type") == "reply":
                reply_id = seg.get("data", {}).get("id")
                if reply_id is not None:
                    try:
                        return int(reply_id)
                    except (ValueError, TypeError):
                        # Some OneBot implementations use string IDs
                        return str(reply_id)
        return None

    @staticmethod
    def _get_reply_inline_text(msg: dict) -> Optional[str]:
        """Extract quoted text directly from the reply segment data.
        
        Many OneBot implementations (NapCat, Shamrock, LLOneBot) include
        the quoted message text inline in the reply segment's data.text or
        data.message field. This avoids needing to fetch it separately.
        """
        segments = msg.get("message", [])
        if not isinstance(segments, list):
            return None
        for seg in segments:
            if seg.get("type") == "reply":
                data = seg.get("data", {})
                for field in ("message", "text", "content", "desc"):
                    val = data.get(field)
                    if val and isinstance(val, str) and len(val.strip()) > 1:
                        # Skip placeholder values like "[图片]" only
                        if val.strip() in ("[图片]", "[视频]", "[语音]", "[文件]"):
                            continue
                        return val.strip()
        return None

    def _cq_to_readable(self, text: str) -> str:
        if not text:
            return ""
        text = re.sub(r'\[CQ:at,qq=(\d+)\]', r'@QQ\1', text)
        text = re.sub(r'\[CQ:at,qq=all\]', '@全体成员', text)
        text = re.sub(r'\[CQ:reply,id=\d+\]', '[回复消息]', text)
        text = re.sub(r'\[CQ:image,[^\]]*\]', '[图片]', text)
        text = re.sub(r'\[CQ:face,id=(\d+)[^\]]*\]', lambda m: _qq_face_text(m.group(1)), text)
        text = re.sub(r'\[CQ:video,[^\]]*\]', '[视频]', text)
        text = re.sub(r'\[CQ:file,[^\]]*\]', '[文件]', text)
        text = re.sub(r'\[CQ:json,([^\]]*)\]', self._parse_json_card, text)
        text = re.sub(r'\[CQ:xml,([^\]]*)\]', self._parse_xml_card, text)
        text = re.sub(r'\[CQ:[^\]]+\]', '', text)
        return text.strip()

    @staticmethod
    def _parse_json_card(m: re.Match) -> str:
        raw = m.group(1) or ""
        if not raw:
            return "[分享]"
        try:
            import json as _j
            import urllib.parse as _u
            decoded = _u.unquote(raw)
            data = _j.loads(decoded)
            title = data.get("title", "") or data.get("prompt", "")
            desc = data.get("desc", "") or data.get("summary", "")
            url = data.get("jumpUrl", "") or data.get("url", "")
            parts = ["[分享]"]
            if title:
                parts.append(title[:60])
            if desc and desc != title:
                parts.append(desc[:80])
            if url:
                parts.append(url[:120])
            return " ".join(parts) if len(parts) > 1 else "[分享]"
        except Exception:
            return "[分享]"

    @staticmethod
    def _parse_xml_card(m: re.Match) -> str:
        raw = m.group(1) or ""
        if not raw:
            return "[卡片]"
        try:
            import urllib.parse as _u
            decoded = _u.unquote(raw)
            title_m = re.search(r'<title>([^<]*)</title>', decoded) or re.search(r'title="([^"]*)"', decoded)
            desc_m = re.search(r'<desc>([^<]*)</desc>', decoded) or re.search(r'desc="([^"]*)"', decoded)
            parts = ["[卡片]"]
            if title_m:
                parts.append(title_m.group(1)[:60])
            if desc_m and desc_m.group(1) != (title_m.group(1) if title_m else ""):
                parts.append(desc_m.group(1)[:80])
            return " ".join(parts) if len(parts) > 1 else "[卡片]"
        except Exception:
            return "[卡片]"

    # ── STM → Episodic Interface ─────────────────────────────────

    def _write_episodic_segment(self, group_id: str):
        """Archive the previous conversation segment as an episodic memory entry.

        Called when a new wake trigger fires after >5min silence — the old
        episode has ended. Collects messages since the last bot reply and
        writes a single episodic row to LTM for L3 sleep loop processing.
        """
        try:
            from agent.memory.long_term import LongTermMemory
            from agent.memory.store import MemoryStore
            import json as _json, sqlite3 as _sqlite3

            store = MemoryStore()
            ltm = LongTermMemory(store)

            last_reply = self._last_bot_reply.get(group_id)
            if not last_reply:
                return
            since_ts = last_reply[0] - 600

            state_db = str(get_state_db_path())
            sdb = _sqlite3.connect(state_db, timeout=10)
            raw_msgs = sdb.execute(
                """SELECT sender_name, content_readable, is_bot, created_at, user_id
                   FROM corpus_messages
                   WHERE group_id = ? AND created_at > ? AND created_at <= ?
                   ORDER BY created_at ASC LIMIT 50""",
                (group_id, since_ts, time.time()),
            ).fetchall()
            sdb.close()
            msgs = [(r[0], r[1], r[2], r[3]) for r in raw_msgs]

            if len(msgs) < 2:
                return

            participants = list(set(m[0] for m in msgs if m[0] != "bot"))
            time_range = f"{msgs[0][3]:.0f}-{msgs[-1][3]:.0f}" if msgs else ""
            text = "\n".join(f"{m[0]}: {m[1][:100]}" for m in msgs)

            try:
                from agent.memory.date_resolver import resolve_relative_dates
                text, _ = resolve_relative_dates(text, msgs[0][3] if msgs else time.time())
            except Exception:
                pass

            first_user_id = ""
            for r in raw_msgs:
                if r[2] == 0 and r[4]:
                    first_user_id = str(r[4])
                    break

            contains_correction = False
            for _, content, _, _ in msgs:
                if any(w in (content or "").lower() for w in
                       ["搞错了", "不对", "不是", "记错了", "更正", "纠正", "早就", "过了"]):
                    contains_correction = True
                    break

            type_data = _json.dumps({
                "participants": participants,
                "time_range": [msgs[0][3], msgs[-1][3]] if msgs else [],
                "topic": "",
                "turn_count": len(msgs),
                "source_msg_ids": [],
                "contains_correction": contains_correction,
                "intent": "group_chat",
                "source": "adapter_episodic",
            }, ensure_ascii=False)

            ltm.add_fact(
                category="general",
                key=f"ep_{group_id}_{int(time.time())}",
                value=text[:2000],
                confidence=0.3,
                derivation="direct",
                memory_type="episodic",
                type_data=_json.loads(type_data),
                salience=0.4,
                source_user_id=first_user_id,
                source_message_ts=time_range,
                source_context=f"group:{group_id}",
            )
            logger.info("[OneBot] Episode written: group=%s msgs=%d correction=%s",
                       group_id, len(msgs), contains_correction)
        except Exception as e:
            logger.warning("[OneBot] Episode write failed: %s", e)

    def _generate_group_topic_summary(self, group_id: str):
        try:
            import sqlite3 as _sql
            import json as _json
            state_db = str(get_state_db_path())
            sdb = _sql.connect(state_db, timeout=10)
            rows = sdb.execute(
                """SELECT sender_name, content_readable, created_at
                   FROM corpus_messages
                   WHERE group_id = ? AND created_at > ?
                   ORDER BY created_at ASC LIMIT 100""",
                (group_id, time.time() - 3600),
            ).fetchall()
            sdb.close()
            if len(rows) < 3:
                return
            from collections import Counter
            import re as _re
            all_text = " ".join(r[1] or "" for r in rows)
            cjk = _re.findall(r'[\u4e00-\u9fff]{2,4}', all_text)
            keywords = [w for w, c in Counter(cjk).most_common(10) if c >= 2]
            summary_parts = []
            seen = set()
            for r in rows[-20:]:
                name = r[0]
                text = (r[1] or "")[:60]
                if text and text not in seen:
                    seen.add(text)
                    summary_parts.append(f"{name}: {text}")
            summary = "\n".join(summary_parts[:10])
            sdb = _sql.connect(state_db, timeout=10)
            sdb.execute(
                "UPDATE groups_registry SET topic_summary = ?, topic_keywords = ?, topic_updated_at = ? WHERE group_id = ?",
                (_json.dumps(summary, ensure_ascii=False)[:2000],
                 _json.dumps(keywords, ensure_ascii=False),
                 time.time(), group_id),
            )
            sdb.commit()
            sdb.close()
            logger.info("[OneBot] Topic summary for %s: %d keywords, %d chars",
                        group_id, len(keywords), len(summary))
        except Exception as e:
            logger.debug("[OneBot] Topic summary failed: %s", e)

    # ------------------------------------------------------------------
    # Message processing
    # ------------------------------------------------------------------

    async def _get_voice_file(self, msg: dict) -> Optional[str]:
        """Download a voice message from OneBot and return the local file path."""
        segments = msg.get("message", [])
        if not isinstance(segments, list):
            return None

        for seg in segments:
            if seg.get("type") in ("record", "voice"):
                file_url = seg.get("data", {}).get("url", "")
                file_id = seg.get("data", {}).get("file", "")

                if file_url:
                    # Download via HTTP
                    import httpx
                    try:
                        async with httpx.AsyncClient(timeout=30.0) as client:
                            resp = await client.get(file_url)
                            resp.raise_for_status()
                            # Determine extension from content-type or default to .ogg
                            content_type = resp.headers.get("content-type", "")
                            if "amr" in content_type:
                                ext = ".amr"
                            elif "wav" in content_type:
                                ext = ".wav"
                            elif "mp3" in content_type:
                                ext = ".mp3"
                            else:
                                ext = ".ogg"

                            from gateway.platforms.base import get_audio_cache_dir
                            cache_dir = get_audio_cache_dir()
                            filename = f"onebot_{msg.get('message_id', 'unknown')}{ext}"
                            filepath = cache_dir / filename
                            filepath.write_bytes(resp.content)
                            logger.info("[OneBot] Downloaded voice file: %s (%d bytes)", filepath, len(resp.content))
                            return str(filepath)
                    except Exception as e:
                        logger.warning("[OneBot] Failed to download voice file from URL: %s", e)

                if file_id:
                    # Use get_file API to get the file
                    try:
                        file_result = await self._send_action("get_file", {"file_id": file_id})
                        file_data = file_result.get("data", {})
                        file_content = file_data.get("file", "")
                        if file_content:
                            from gateway.platforms.base import get_audio_cache_dir
                            cache_dir = get_audio_cache_dir()
                            filename = f"onebot_{msg.get('message_id', 'unknown')}.ogg"
                            filepath = cache_dir / filename
                            if isinstance(file_content, bytes):
                                filepath.write_bytes(file_content)
                            else:
                                filepath.write_text(file_content)
                            logger.info("[OneBot] Downloaded voice file via get_file: %s", filepath)
                            return str(filepath)
                    except Exception as e:
                        logger.warning("[OneBot] Failed to get voice file via get_file: %s", e)

                # Fallback: try to get file URL via get_record_msg or similar
                logger.warning("[OneBot] Voice segment found but no URL or file_id available")
                return None

        return None

    def _has_voice_message(self, msg: dict) -> bool:
        """Check if the message contains a voice/record segment."""
        segments = msg.get("message", [])
        if not isinstance(segments, list):
            return False
        return any(seg.get("type") in ("record", "voice") for seg in segments)

    def _has_image_message(self, msg: dict) -> bool:
        segments = msg.get("message", [])
        if not isinstance(segments, list):
            return False
        # Per OneBot v11: type=face is a QQ SYSTEM emoji (inline glyph, id only),
        # not an image. Including it here triggers a vision API call on a
        # non-existent file path that returns "图片", polluting context.
        return any(seg.get("type") in ("image", "mface") for seg in segments)

    def _has_sticker_message(self, msg: dict) -> bool:
        segments = msg.get("message", [])
        if not isinstance(segments, list):
            return False
        return any(seg.get("type") == "mface" for seg in segments)

    def _has_video_message(self, msg: dict) -> bool:
        segments = msg.get("message", [])
        if not isinstance(segments, list):
            return False
        return any(seg.get("type") == "video" for seg in segments)

    def _group_uid_name_map(self, group_id: str) -> dict:
        """Build uid→display-name map from recent group buffer entries."""
        gs = self._group_states.get(group_id)
        if gs is None:
            return {}
        mapping = {}
        for m in gs.get_recent():
            if m.uid and m.name and m.uid not in mapping:
                mapping[m.uid] = m.name
        return mapping

    @staticmethod
    def _get_seg_data(seg: dict, key: str, default=""):
        """Safely get segment data field, handling JSON null."""
        data = seg.get("data")
        if not isinstance(data, dict):
            return default
        return data.get(key, default)

    @staticmethod
    def _mime_for_ext(ext: str) -> str:
        mapping = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                   ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}
        return mapping.get(ext.lower(), "image/jpeg")

    def _transcribe_voice(self, voice_path: str) -> str:
        """Transcribe a voice file using FunASR SenseVoiceSmall. Cached per path."""
        if voice_path in self._voice_transcripts:
            return self._voice_transcripts[voice_path]
        if not os.path.exists(voice_path):
            return "语音"
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path.home() / ".hermes" / "tools"))
            from stt_funasr import transcribe
            result = transcribe(voice_path)
            text = result.get("text", "").strip()
            if text and text != "语音":
                self._voice_transcripts[voice_path] = text
                return text
        except Exception as e:
            logger.warning("[OneBot] Voice transcription failed for %s: %s", voice_path, e)
        self._voice_transcripts[voice_path] = "语音"
        return "语音"

    async def _describe_image(self, image_path: str, is_sticker: bool = False) -> str:
        """Describe an image via cloud MiMo v2.5. Cached per path, 500 FIFO."""
        if image_path in self._image_descriptions:
            return self._image_descriptions[image_path]
        if not os.path.exists(image_path):
            self._image_descriptions[image_path] = "图片"
            return "图片"

        try:
            api_key = os.getenv("XIAOMI_API_KEY", "")
            api_base = os.getenv("XIAOMI_BASE_URL", "")
            api_model = os.getenv("XIAOMI_MODEL", "")
            if not api_key or not api_base or not api_model:
                self._image_descriptions[image_path] = "图片"
                return "图片"

            import base64
            with open(image_path, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode()
            ext = os.path.splitext(image_path)[1].lower()
            mime = self._mime_for_ext(ext)

            desc_prompt = (
                "QQ群聊中有人发了这张图。请判断发送者想通过这张图表达什么情绪或态度"
                "（大概率是表情包/梗图/反应图/贴纸，极少数是实拍分享）。\n"
                "请只用1-3个词描述发送者的情绪意图。例如：开心、害羞、惊讶、装傻、得意、委屈、"
                "生气、疑问、无语、鄙视、哭、可怜、贴贴、比心、笑死、急了、切割、怂了、白嫖、不愧是你。\n"
                "只在极其明显不是情绪图时（风景照、商品图、截图、文档），才用最短文字描述画面内容。\n"
                "中文，不超过10字。只输出情绪词或简短描述本身，不要任何解释前缀。"
            )
            max_desc_len = 12

            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                resp = await client.post(
                    f"{api_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": api_model,
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": desc_prompt},
                                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                            ],
                        }],
                        "max_tokens": 65536,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                desc = (data["choices"][0]["message"].get("content") or "").strip()
                if not desc:
                    reasoning = data["choices"][0]["message"].get("reasoning_content") or ""
                    desc = reasoning[:40].strip()
        except Exception as e:
            logger.warning(
                "[OneBot] Image description failed for %s: %s (type=%s)",
                image_path, e, type(e).__name__,
            )
            if hasattr(e, 'response') and e.response is not None:
                try:
                    _body = e.response.text[:500] if hasattr(e.response, 'text') else ''
                    logger.warning("[OneBot] Image desc HTTP response: status=%s body=%s",
                                   getattr(e.response, 'status_code', '?'), _body)
                except Exception:
                    pass
            desc = "图片"

        if len(self._image_descriptions) > 500:
            _oldest = next(iter(self._image_descriptions))
            del self._image_descriptions[_oldest]
        self._image_descriptions[image_path] = desc
        return desc

    async def _describe_video(self, video_path: str) -> str:
        """Describe a video via cloud MiMo v2.5. Cached per path, 500 FIFO."""
        if video_path in self._image_descriptions:
            return self._image_descriptions[video_path]
        if not os.path.exists(video_path):
            self._image_descriptions[video_path] = "视频"
            return "视频"

        try:
            api_key = os.getenv("XIAOMI_API_KEY", "")
            api_base = os.getenv("XIAOMI_BASE_URL", "")
            api_model = os.getenv("XIAOMI_MODEL", "")
            if not api_key or not api_base or not api_model:
                self._image_descriptions[video_path] = "视频"
                return "视频"

            import base64
            with open(video_path, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode()
            ext = os.path.splitext(video_path)[1].lower()
            video_mimes = {".mp4": "video/mp4", ".mov": "video/quicktime", ".avi": "video/x-msvideo", ".mkv": "video/x-matroska", ".3gp": "video/3gpp"}
            mime = video_mimes.get(ext, "video/mp4")

            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                resp = await client.post(
                    f"{api_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": api_model,
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "简洁描述这个视频的内容，包括画面、动作、声音。中文，不超过50字。"},
                                {"type": "video_url", "video_url": {"url": f"data:{mime};base64,{b64}"}},
                            ],
                        }],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                desc = (data["choices"][0]["message"].get("content") or "").strip()
                if not desc:
                    reasoning = data["choices"][0]["message"].get("reasoning_content") or ""
                    desc = reasoning[:50].strip()
        except Exception as e:
            logger.warning("[OneBot] Video description failed for %s: %s", video_path, e)
            desc = "视频"

        if len(self._image_descriptions) > 500:
            _oldest = next(iter(self._image_descriptions))
            del self._image_descriptions[_oldest]
        self._image_descriptions[video_path] = desc
        return desc

    async def _transcribe_voice_mimo(self, voice_path: str) -> str:
        """Transcribe voice via cloud MiMo v2.5 audio capability.

        Fallback to local FunASR if MiMo unavailable.
        """
        if voice_path in self._voice_transcripts:
            return self._voice_transcripts[voice_path]
        if not os.path.exists(voice_path):
            return "语音"

        try:
            api_key = os.getenv("XIAOMI_API_KEY", "")
            api_base = os.getenv("XIAOMI_BASE_URL", "")
            api_model = os.getenv("XIAOMI_MODEL", "")
            if not api_key or not api_base or not api_model:
                return self._transcribe_voice(voice_path)

            import base64
            with open(voice_path, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode()
            ext = os.path.splitext(voice_path)[1].lower()
            audio_mimes = {".ogg": "audio/ogg", ".opus": "audio/ogg", ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".amr": "audio/amr", ".silk": "audio/silk"}
            mime = audio_mimes.get(ext, "audio/ogg")

            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                resp = await client.post(
                    f"{api_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": api_model,
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "请将这段语音转写为文字。只输出转写结果，不要解释。中文。"},
                                # opencodego relay forwards to Xiaomi MiMo; the
                                # OpenAI-realtime `input_audio` schema is rejected
                                # with "invalid audio format". MiMo accepts the
                                # image-style `audio_url` carrying a data URL.
                                {"type": "audio_url", "audio_url": {"url": f"data:{mime};base64,{b64}"}},
                            ],
                        }],
                        "max_tokens": 65536,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                text = (data["choices"][0]["message"].get("content") or "").strip()
                if text and text != "语音":
                    self._voice_transcripts[voice_path] = text
                    return text
        except Exception as e:
            logger.warning("[OneBot] MiMo voice transcription failed for %s: %s", voice_path, e)

        return self._transcribe_voice(voice_path)

    async def _get_image_files(self, msg: dict) -> list:
        """Download image(s) from OneBot message and return local file paths."""
        from gateway.platforms.base import cache_image_from_bytes
        from urllib.parse import unquote
        segments = msg.get("message", [])
        if not isinstance(segments, list):
            return []

        paths = []
        for seg in segments:
            _seg_type = seg.get("type", "")
            # face = QQ system emoji (OneBot v11 spec: id only, no url/file).
            # Skipping it here means no pseudo-path/no vision API call;
            # `_cq_to_readable` already turned [CQ:face,id=N] into [喷血] etc.
            # in the text the AI actually sees.
            # Animated emoji (动画表情, emoji-recv) arrive as face segments
            # WITH url/file (sub_type=1) — download them, not skip.
            if _seg_type == "face":
                _face_url = self._get_seg_data(seg, "url", "")
                _face_file = self._get_seg_data(seg, "file", "")
                if not _face_url and not _face_file:
                    continue
                _seg_type = "image"
            if _seg_type not in ("image", "mface"):
                continue

            file_url = self._get_seg_data(seg, "url", "")
            file_id = self._get_seg_data(seg, "file", "")
            # Determine extension from summary or default to .jpg
            summary = self._get_seg_data(seg, "summary", "")
            ext = ".jpg"
            if summary and "gif" in summary.lower():
                ext = ".gif"
            elif summary and "png" in summary.lower():
                ext = ".png"

            logger.info("[OneBot] Image segment data: url=%s, file=%s, summary=%s", 
                       file_url[:100] if file_url else "(empty)", 
                       file_id[:50] if file_id else "(empty)",
                       summary[:50] if summary else "(empty)")

            # Try 1: Download from URL (could be http://, https://, or file:///)
            if file_url:
                import httpx
                try:
                    # Handle file:/// and file:// URLs
                    if file_url.startswith("file://"):
                        local_path = unquote(file_url[7:] if file_url.startswith("file:///") else file_url[5:])
                        # Normalize Windows path (strip leading / for file:///C:/...)
                        if local_path.startswith("/"):
                            local_path = local_path[1:]
                        if os.path.exists(local_path):
                            with open(local_path, "rb") as f:
                                img_data = f.read()
                            cached_path = cache_image_from_bytes(img_data, ext=ext)
                            paths.append(cached_path)
                            logger.info("[OneBot] Loaded image from local file: %s", cached_path)
                            continue
                        else:
                            logger.warning("[OneBot] Local file not found: %s", local_path)
                    else:
                        # HTTP/HTTPS URL
                        async with httpx.AsyncClient(timeout=30.0) as client:
                            resp = await client.get(file_url)
                            resp.raise_for_status()
                            cached_path = cache_image_from_bytes(resp.content, ext=ext)
                            paths.append(cached_path)
                            logger.info("[OneBot] Downloaded image from URL: %s (%d bytes)", cached_path, len(resp.content))
                            continue
                except Exception as e:
                    logger.warning("[OneBot] Failed to download image from URL: %s", e)

            # Try 2: Use get_file API (primary fallback for expired CDN URLs)
            if file_id:
                try:
                    # Try with full file_id first, then without extension
                    for fid in (file_id, file_id.rsplit(".", 1)[0] if "." in file_id else file_id):
                        try:
                            file_result = await self._send_action("get_file", {"file_id": fid})
                            file_data = file_result.get("data", {})
                            file_content = file_data.get("file", "")
                            file_url_api = file_data.get("url", "")
                            if file_content or file_url_api:
                                break
                        except Exception:
                            continue
                    
                    # NapCat may return file content as base64 or a local path
                    file_content = file_data.get("file", "")
                    file_url_api = file_data.get("url", "")
                    
                    if file_content:
                        # Could be base64 or raw bytes
                        if isinstance(file_content, bytes):
                            img_data = file_content
                        elif isinstance(file_content, str):
                            import base64
                            # Try base64 first
                            try:
                                img_data = base64.b64decode(file_content)
                            except Exception:
                                # Check if it's a file path
                                if len(file_content) < 500 and os.path.exists(file_content):
                                    with open(file_content, "rb") as f:
                                        img_data = f.read()
                                else:
                                    img_data = file_content.encode()
                        else:
                            img_data = bytes(file_content)
                        
                        cached_path = cache_image_from_bytes(img_data, ext=ext)
                        paths.append(cached_path)
                        logger.info("[OneBot] Downloaded image via get_file: %s", cached_path)
                        continue
                    elif file_url_api:
                        # Fallback to URL from get_file response
                        import httpx
                        async with httpx.AsyncClient(timeout=30.0) as client:
                            resp = await client.get(file_url_api)
                            resp.raise_for_status()
                            cached_path = cache_image_from_bytes(resp.content, ext=ext)
                            paths.append(cached_path)
                            logger.info("[OneBot] Downloaded image via get_file URL: %s", cached_path)
                            continue
                except Exception as e:
                    logger.warning("[OneBot] Failed to get image via get_file: %s", e)

            logger.warning("[OneBot] Image segment found but could not download. Data: %s", 
                          json.dumps(seg.get("data", {}), ensure_ascii=False)[:200])

        return paths

    # ------------------------------------------------------------------
    # Image+text batching (debounce follow-up text after image)
    # ------------------------------------------------------------------

    def _image_batch_key(self, event: MessageEvent) -> str:
        """Session-scoped key for image+text batching."""
        from gateway.session import build_session_key
        return build_session_key(
            event.source,
            group_sessions_per_user=self.config.extra.get("group_sessions_per_user", True),
            thread_sessions_per_user=self.config.extra.get("thread_sessions_per_user", False),
        )

    def _enqueue_image_event(self, event: MessageEvent) -> None:
        """Buffer an image event and start the flush timer.

        When a user sends an image followed quickly by text (e.g., "这个是"),
        this waits for the quiet period so both can be processed together.
        """
        key = self._image_batch_key(event)
        self._pending_image_events[key] = event

        # Cancel any pending flush and restart the timer
        prior_task = self._pending_image_tasks.get(key)
        if prior_task and not prior_task.done():
            prior_task.cancel()
        self._pending_image_tasks[key] = asyncio.create_task(
            self._flush_image_batch(key)
        )

    async def _flush_image_batch(self, key: str) -> None:
        """Wait for the quiet period then dispatch the aggregated image event."""
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(self._image_text_delay_seconds)
            event = self._pending_image_events.pop(key, None)
            if not event:
                return
            logger.info(
                "[OneBot] Flushing image batch %s (text_len=%d, images=%d)",
                key, len(event.text or ""), len(event.media_urls or []),
            )
            _gid = _chat_id if _chat_id.startswith("group:") else ""
            if _gid and _chat_id.startswith("group:"):
                _gid = _chat_id.split(":", 1)[1]
                from .trigger_coordinator import TriggerRequest
                self._group_executor.schedule(TriggerRequest(
                    group_id=_gid, origin_seq=0, mode="image",
                    decision_reason="图片消息", raw_msg=event.raw_message or {},
                ))
            else:
                await self.handle_message(event)
        except asyncio.CancelledError:
            pass
        finally:
            if self._pending_image_tasks.get(key) is current_task:
                self._pending_image_tasks.pop(key, None)

    def _try_merge_text_into_pending_image(self, session_key: str, text_event: MessageEvent) -> bool:
        """If there's a pending image event for this session, merge text into it.

        Returns True if text was merged (caller should skip normal dispatch).
        """
        pending = self._pending_image_events.get(session_key)
        if pending is None:
            return False

        # Merge text into the pending image event
        if text_event.text:
            if pending.text:
                pending.text = f"{pending.text}\n{text_event.text}"
            else:
                pending.text = text_event.text

        # Reset the flush timer
        key = session_key
        prior_task = self._pending_image_tasks.get(key)
        if prior_task and not prior_task.done():
            prior_task.cancel()
        self._pending_image_tasks[key] = asyncio.create_task(
            self._flush_image_batch(key)
        )

        logger.info(
            "[OneBot] Merged text into pending image event %s (now: text_len=%d)",
            key, len(pending.text or ""),
        )
        return True

    async def _flush_mention_batch(self, key: str, group_id):
        """Flush accumulated @mentions as one merged message after batch delay."""
        await asyncio.sleep(self._mention_batch_delay)
        entries = self._pending_mentions.pop(key, [])
        if not entries:
            return
        # Merge all mention texts (strip CQ codes) and collect images
        texts = [f"{e['name']}: {self._cq_to_readable(e['text'][:100])}" for e in entries]
        # Download images from merged entries
        all_images = []
        for e in entries:
            if self._has_image_message(e.get("msg", {})):
                try:
                    imgs = await self._get_image_files(e["msg"])
                    if imgs:
                        all_images.extend(imgs)
                except Exception:
                    pass
        merged_text = "\n".join(texts)
        logger.info("[OneBot] Flushing %d merged @mentions for group %s", len(entries), group_id)
        # Use the last msg as template, replace text with merged content
        last = entries[-1]
        msg = dict(last["msg"])
        _self_qq = str(self._self_id or "")
        msg["raw_message"] = f"[CQ:at,qq={_self_qq}] {merged_text}"
        msg["message"] = [
            {"type": "at", "data": {"qq": _self_qq}},
            {"type": "text", "data": {"text": f"[合并消息，{len(entries)}人@]: {merged_text}"}}
        ]
        # Re-process without batching — include images in merged message
        merged_msg_arr = [
            {"type": "at", "data": {"qq": _self_qq}},
            {"type": "text", "data": {"text": f"[合并消息，{len(entries)}人@]: {merged_text}"}}
        ]
        # Attach original image/face/mface segments so _get_image_files can process them
        for e in entries:
            for seg in e.get("msg", {}).get("message", []):
                if isinstance(seg, dict) and seg.get("type") in ("image", "mface"):
                    merged_msg_arr.append(seg)
        msg["message"] = merged_msg_arr
        msg["_skip_mention_batch"] = True
        msg["_skip_dedup"] = True
        await self._process_message(msg)

    async def _dispatch_to_agent(self, event) -> None:
        await self.handle_message(event)

    async def _update_rolling_summary(self, group_id: str) -> None:
        _gs = self._group_states.get(group_id)
        _recent = _gs.get_recent()
        if not _recent:
            return
        _self_id_str = str(self._self_id) if self._self_id else ""
        _bot_name = self._bot_name

        def _annotate_at(text: str) -> str:
            if not _self_id_str or not text:
                return text
            text = re.sub(r'@QQ' + re.escape(_self_id_str) + r'(?!\d)', f'@{_bot_name}', text)
            text = re.sub(r'@QQ\d+(?!\d)', '@群友', text)
            return text

        _recent_dicts = [
            {"ts_str": time.strftime('%m-%d %H:%M', time.localtime(m.ts)),
             "name": m.name, "text": _annotate_at(m.text)[:150], "is_bot": m.is_bot}
            for m in _recent[-20:]
        ]
        try:
            from .semantic_judge import generate_rolling_summary
            _gs.rolling_summary = await generate_rolling_summary(
                _recent_dicts, prev_summary=_gs.rolling_summary
            )
        except Exception as e:
            logger.warning("[OneBot] Rolling summary failed: %s", e)

    async def _recover_missed_messages(self):
        """After reconnect, fetch recent group history and respond to fresh @mentions."""
        await asyncio.sleep(2)
        now = time.time()
        # Load persisted last-seen timestamps
        try:
            import json as _json
            _p = self._cache_dir / "last_seen.json"
            if _p.exists():
                self._last_seen_ts = _json.loads(_p.read_text(encoding="utf-8"))
        except Exception:
            pass
        # Get known groups: in-memory first, then SQLite fallback
        groups = list(self._group_states.get_all().keys())
        if not groups:
            try:
                from agent.memory.gateway import UnifiedMemoryGateway
                gw = UnifiedMemoryGateway.get_instance()
                rows = gw._store.get_all_chat_ids("group")
                groups = list(set(r[0] for r in rows))
            except Exception:
                pass
        for group_id in groups:
            try:
                # Track last-seen timestamp per group for gap detection
                last_seen = getattr(self, '_last_seen_ts', {}).get(group_id, 0)
                all_messages = []
                # Fetch in batches to get more history
                for count in (200, 150, 100):
                    hist = await self._send_action("get_group_msg_history", {
                        "group_id": int(group_id), "count": count,
                    })
                    msgs = hist.get("data", {}).get("messages", []) or []
                    if len(msgs) >= count - 10:
                        continue  # hit the limit, try smaller
                    all_messages = msgs
                    break
                if not all_messages:
                    all_messages = hist.get("data", {}).get("messages", []) or []
                
                if not all_messages:
                    continue
                
                # Sort by time (oldest first)
                all_messages.sort(key=lambda m: m.get("time", 0))
                logger.info("[OneBot] Recovered %d messages for group %s after reconnect", len(all_messages), group_id)
                
                # Detect gaps and insert placeholder
                filled = []
                prev_ts = last_seen if last_seen > 0 else all_messages[0].get("time", 0)
                for m in all_messages:
                    msg_ts = m.get("time", 0)
                    gap = msg_ts - prev_ts
                    if gap > 120 and prev_ts > 0:  # >2 min gap = lost context
                        filled.append({
                            "time": int(prev_ts + 60),
                            "user_id": 0,
                            "sender": {"nickname": "[系统]"},
                            "raw_message": "⚠ 掉线期间消息丢失，上下文不完整",
                            "_is_placeholder": True,
                        })
                    filled.append(m)
                    prev_ts = msg_ts
                
                # Store all messages
                for m in filled:
                    msg_time = m.get("time", 0)
                    sender = m.get("sender", {})
                    sid = str(m.get("user_id", ""))
                    sname = sender.get("card") or sender.get("nickname") or f"QQ{sid}"
                    text = m.get("raw_message") or ""
                    is_bot = str(sid) == str(self._self_id)
                    # Skip image-only messages (no text content for agent to process)
                    if m.get("message") and all(s.get("type") in ("image", "at") for s in m.get("message", [])):
                        is_image_only = not any(s.get("type") == "text" for s in m.get("message", []))
                        if is_image_only:
                            text = "[图片]" if not text else text
                    ts = float(msg_time) if msg_time > 1000000 else now
                    # Store full text — QQ messages are already size-limited by protocol
                    buf_text = text
                    _mid = str(m.get("real_id", m.get("message_id", "")))
                    try:
                        from .group_state import BufferedMessage
                        self._group_states.get(group_id).append_message(
                            BufferedMessage(mid=_mid, ts=ts, uid=str(sid), name=sname, text=buf_text)
                        )
                    except Exception:
                        pass
                    self._persist_chat_message(group_id, "group", int(sid or 0), sname, text,
                                               message_id=str(m.get("real_id", m.get("message_id", ""))),
                                               created_at=ts,
                                               content_raw=text,
                                               sender_card=sender.get("card", ""))
                    if m.get("_is_placeholder"):
                        continue
                    if self._is_mentioned(m, self._self_id or 0):
                        last_other_ts = 0
                        _gs = self._group_states.get(group_id)
                        for bm in reversed(_gs.buffer[:-1]):
                            if bm.name != "bot":
                                last_other_ts = bm.ts
                                break
                        age = now - ts
                        gap_since_last = now - last_other_ts if last_other_ts else 999
                        still_active = gap_since_last < 180  # someone talking <3 min ago
                        if age < 180 and still_active:
                            logger.info("[OneBot] Recovered @mention (%.0fs old): %s", age, text[:80])
                            try:
                                m["_skip_mention_batch"] = True
                                m["_skip_reply_context"] = True  # historical, replay fetch will fail
                                await self._process_message(m)
                            except Exception:
                                pass
                
                if last_seen > 0:
                    if not hasattr(self, '_last_seen_ts'):
                        self._last_seen_ts = {}
                    self._last_seen_ts[group_id] = now
                    # Persist to file
                    try:
                        import json as _json
                        _p = self._cache_dir / "last_seen.json"
                        _p.parent.mkdir(parents=True, exist_ok=True)
                        existing = _json.loads(_p.read_text(encoding="utf-8")) if _p.exists() else {}
                        existing.update(self._last_seen_ts)
                        _p.write_text(_json.dumps(existing), encoding="utf-8")
                    except Exception:
                        pass
            except Exception as e:
                logger.debug("[OneBot] Message recovery failed for group %s: %s", group_id, e)

    # ── Concurrency & dedup helpers ─────────────────────────
    def _get_group_lock(self, group_id: str) -> asyncio.Lock:
        """Get (or create) a per-group asyncio.Lock for serial processing.
        
        Ensures only one agent runs per group at a time, preventing
        concurrent messages from interfering with each other's context.
        Pattern borrowed from Feishu adapter's _chat_locks.
        """
        if group_id not in self._group_locks:
            self._group_locks[group_id] = asyncio.Lock()
        return self._group_locks[group_id]

    def _get_dm_lock(self, user_id: str) -> asyncio.Lock:
        """Get (or create) a per-user asyncio.Lock for serial private message processing."""
        if user_id not in self._dm_locks:
            self._dm_locks[user_id] = asyncio.Lock()
        return self._dm_locks[user_id]

    def _is_duplicate(self, msg_id: str) -> bool:
        """Check if a message was already processed within dedup TTL.
        
        Returns True if this message_id was seen in the last 5 minutes.
        Also prunes expired entries to prevent unbounded memory growth.
        """
        now = time.time()
        # Prune expired entries (every ~100 messages)
        if len(self._seen_msg_ids) > 100:
            self._seen_msg_ids = {
                mid: ts for mid, ts in self._seen_msg_ids.items()
                if now - ts < self._DEDUP_TTL
            }
        if msg_id in self._seen_msg_ids:
            if now - self._seen_msg_ids[msg_id] < self._DEDUP_TTL:
                return True
        self._seen_msg_ids[msg_id] = now
        return False

    async def _process_message_bounded(self, msg: dict) -> None:
        async with self._ingress_semaphore:
            await self._process_message(msg)

    async def _process_message(self, msg: dict) -> None:
        """Process an incoming message event."""
        user_id = msg.get("user_id")
        group_id = str(msg.get("group_id", ""))
        msg_type = msg.get("message_type", "")
        self_id = msg.get("self_id")

        logger.info("[OneBot] _process_message: user_id=%s, group_id=%s, msg_type=%s, self_id=%s",
                    user_id, group_id, msg_type, self_id)

        if user_id is None or self_id is None:
            return  # malformed message, silently drop

        # Dedup: skip if this message was already processed recently
        # Skip dedup for merged @mention batches (they have a synthetic message_id)
        msg_id = str(msg.get("message_id", ""))
        if msg_id and not msg.get("_skip_dedup") and self._is_duplicate(msg_id):
            logger.debug("[OneBot] Skipping duplicate message %s", msg_id)
            return

        _fwd_id = self._extract_forward_id(msg)
        if _fwd_id:
            now = time.time()
            # Prune expired (every ~50 forward messages)
            if len(self._seen_forward_ids) > 50:
                self._seen_forward_ids = {
                    fid: ts for fid, ts in self._seen_forward_ids.items()
                    if now - ts < 5.0
                }
            if _fwd_id in self._seen_forward_ids:
                if now - self._seen_forward_ids[_fwd_id] < 5.0:
                    logger.info("[OneBot] Skipping duplicate forward sub-message: forward_id=%s", _fwd_id)
                    return
            self._seen_forward_ids[_fwd_id] = now

        if self_id:
            self._self_id = self_id

        if msg_type == "group" and group_id:
            return await self._process_message_impl(msg)
        elif msg_type == "private":
            dm_lock = self._get_dm_lock(str(user_id))
            async with dm_lock:
                return await self._process_message_impl(msg)
        else:
            return await self._process_message_impl(msg)

    @staticmethod
    def _extract_forward_id(msg: dict) -> Optional[str]:
        """Quickly extract forward_id from message segments (no API calls)."""
        segments = msg.get("message", [])
        if not isinstance(segments, list):
            return None
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            st = seg.get("type", "")
            sid = seg.get("data", {}).get("id", "")
            if sid and (st in ("forward", "node") or "content" in seg.get("data", {})):
                return sid
        # Fallback: CQ code in raw_text
        raw = msg.get("raw_message", "")
        if raw:
            fm = re.search(r'\[CQ:forward,id=(\d+)', str(raw))
            if fm:
                return fm.group(1)
        return None

    async def _extract_forward_content(self, msg: dict) -> tuple[str, str, list[str]]:
        """Extract forwarded/merged chat record content from a message.

        Unified extraction for both group and private messages.
        Tries (in order):
        1. NapCat extension: segment data.content (inline, no API call)
        2. get_forward_msg API with message_id param (NapCat convention)
        3. get_forward_msg API with id param (OneBot 11 spec fallback)

        Returns (forward_summary, forward_detail, forward_image_paths).
        Both strings are "" if no forward content was found or extraction failed.
        forward_summary is a compact one-liner for buffers/DB.
        forward_detail is a multi-line block for LLM channel_prompt context.
        """
        segments = msg.get("message", [])
        if not isinstance(segments, list):
            return "", "", []

        _raw_text = self._get_raw_text(msg) or ""

        # Diagnostic: log segment types for empty-text or forward-indicating messages
        _seg_types = [(s.get("type", "?"), list(s.get("data", {}).keys())[:5])
                       for s in segments if isinstance(s, dict)]
        if not _raw_text.strip() or "CQ:forward" in _raw_text or any(
                s.get("type") in ("forward", "node") or "content" in s.get("data", {})
                for s in segments if isinstance(s, dict)):
            logger.info("[OneBot] Forward detection: text=%r, segments=%s, msg_id=%s",
                        _raw_text[:120], _seg_types, msg.get("message_id", "?"))

        # Detect forward ID: prefer segments typed "forward"/"node" or with "content" key
        forward_id = None
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            st = seg.get("type", "")
            sid = self._get_seg_data(seg, "id", "")
            if sid and (st in ("forward", "node") or "content" in seg.get("data", {})):
                forward_id = sid
                break

        # Fallback: CQ code in raw_text (server NapCat sends string format)
        if not forward_id:
            fm = re.search(r'\[CQ:forward,id=(\d+)', _raw_text)
            if fm:
                forward_id = fm.group(1)

        if not forward_id:
            return "", "", []

        fwd_msgs = None
        forward_image_paths: list[str] = []

        # Try 1: NapCat extension - inline content in any segment's data
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            raw_content = seg.get("data", {}).get("content")
            if isinstance(raw_content, list):
                fwd_msgs = raw_content
            elif isinstance(raw_content, str):
                import json as _json
                try:
                    fwd_msgs = _json.loads(raw_content)
                except Exception:
                    pass
            if fwd_msgs:
                break

        # Try 2: get_forward_msg API (fallback for expired or non-inline forwards)
        if not fwd_msgs:
            for param_key in ("message_id", "id"):
                try:
                    fwd_data = await self._send_action("get_forward_msg", {param_key: forward_id})
                    fwd_msgs = fwd_data.get("data", {}).get("messages", [])
                    if fwd_msgs:
                        break
                except Exception as e:
                    logger.debug("[OneBot] get_forward_msg (%s=%s) failed: %s", param_key, forward_id, e)
                    continue

        if not fwd_msgs:
            logger.warning("[OneBot] Forward extraction failed for id=%s", forward_id)
            return "", "", []

        summary_parts = []
        detail_parts = []
        for fm in fwd_msgs:
            await self._expand_forward_message(fm, summary_parts, detail_parts,
                                               forward_image_paths, depth=0)

        if not summary_parts:
            return "", "", []

        forward_summary = "[转发: " + " | ".join(summary_parts) + "]"
        if len(forward_summary) > 4000:
            forward_summary = forward_summary[:4000] + "…[已截断]"
        forward_detail = "[转发消息内容]\n" + "\n".join(detail_parts)
        if len(forward_detail) > _MAX_FORWARD_DETAIL_CHARS:
            forward_detail = await self._compress_forward_detail(forward_detail)

        logger.info("[OneBot] Extracted forward: %d msgs, %d images", len(summary_parts), len(forward_image_paths))
        return forward_summary, forward_detail, forward_image_paths

    async def _expand_forward_message(self, fm: dict, summary_parts: list, detail_parts: list,
                                      forward_image_paths: list, depth: int = 0) -> None:
        """Recursively expand one forwarded message, descending into nested forwards.

        A forwarded transcript may itself contain forward segments (套娃).
        Each level is read fully; content accumulates into summary/detail parts
        with a depth marker so the LLM sees the nesting structure.
        """
        if depth > 5:
            summary_parts.append("[嵌套转发: 层数过深已跳过]")
            return

        name = fm.get("sender", {}).get("nickname", "?")
        _uid = fm.get("user_id") or fm.get("sender", {}).get("user_id", "")
        _prefix = f"{name}(QQ{_uid})" if _uid else name

        # Detect nested forward inside this message (segments or CQ code)
        nested_fwd = None
        segs = fm.get("message", [])
        if isinstance(segs, list):
            for seg in segs:
                if not isinstance(seg, dict):
                    continue
                st = seg.get("type", "")
                sid = self._get_seg_data(seg, "id", "")
                if sid and (st in ("forward", "node") or "content" in seg.get("data", {})):
                    nested_fwd = seg
                    break
        if nested_fwd is None:
            _raw = fm.get("raw_message") or ""
            m = re.search(r'\[CQ:forward,id=(\d+)', str(_raw))
            if m:
                nested_fwd = {"type": "forward", "data": {"id": m.group(1)}}

        if nested_fwd is not None:
            # Nested forward: recurse into its content
            _nested_id = self._get_seg_data(nested_fwd, "id", "") or ""
            if _nested_id and self._seen_forward_ids.get(_nested_id, 0) > time.time() - 5:
                summary_parts.append(f"{_prefix}: [嵌套转发(重复,已跳过)]")
                return
            if _nested_id:
                self._seen_forward_ids[_nested_id] = time.time()

            nested_msgs = None
            raw_content = nested_fwd.get("data", {}).get("content")
            if isinstance(raw_content, list):
                nested_msgs = raw_content
            elif isinstance(raw_content, str):
                import json as _json
                try:
                    nested_msgs = _json.loads(raw_content)
                except Exception:
                    pass
            if not nested_msgs:
                for param_key in ("message_id", "id"):
                    try:
                        fwd_data = await self._send_action("get_forward_msg", {param_key: _nested_id})
                        nested_msgs = fwd_data.get("data", {}).get("messages", [])
                        if nested_msgs:
                            break
                    except Exception:
                        continue

            if nested_msgs:
                inner_summary: list = []
                inner_detail: list = []
                for inner in nested_msgs:
                    await self._expand_forward_message(inner, inner_summary, inner_detail,
                                                       forward_image_paths, depth + 1)
                if inner_summary:
                    summary_parts.append(f"{_prefix}: [嵌套转发: {' | '.join(inner_summary)}]")
                if inner_detail:
                    detail_parts.append(f"┌─ {_prefix} 的嵌套转发:")
                    for line in inner_detail:
                        detail_parts.append(f"│  {line}")
                    detail_parts.append("└─ 嵌套转发结束")
                return

        fwd_text = fm.get("raw_message") or OneBotAdapter._get_text_from_segments(fm)

        # Media stays in its original position: download inline and annotate
        # at this message's location, never hoisted to the top.
        if self._has_image_message(fm):
            try:
                _fwd_imgs = await self._get_image_files(fm)
                if _fwd_imgs:
                    forward_image_paths.extend(_fwd_imgs)
                    _media_note = " [图片:" + ",".join(_fwd_imgs) + "]"
                    fwd_text = (fwd_text or "") + _media_note
                else:
                    fwd_text = (fwd_text or "") + " [图片:下载失败]"
            except Exception:
                fwd_text = (fwd_text or "") + " [图片:下载失败]"
        elif self._has_voice_message(fm):
            fwd_text = (fwd_text or "") + " [语音]"
        elif self._has_video_message(fm):
            fwd_text = (fwd_text or "") + " [视频]"

        fwd_text = self._cq_to_readable(fwd_text or "")
        if not fwd_text:
            return
        summary_parts.append(f"{_prefix}: {fwd_text[:80]}")
        detail_parts.append(f"{name}: {fwd_text}")

    async def _compress_forward_detail(self, detail: str) -> str:
        """Compress an oversized forward transcript via chunked LLM summarisation.

        Splits the transcript into ~30000-char chunks, summarises each with the
        main LLM, and concatenates the summaries — never hard-truncates.
        """
        try:
            import httpx
            api_key = os.getenv("DEEPSEEK_API_KEY", "")
            api_base = os.getenv("DEEPSEEK_BASE_URL", "")
            api_model = os.getenv("DEEPSEEK_MODEL", "")
            if not api_key or not api_base or not api_model:
                return detail[:_MAX_FORWARD_DETAIL_CHARS]

            chunk_size = 30000
            chunks = [detail[i:i + chunk_size] for i in range(0, len(detail), chunk_size)]
            summaries = []
            sys_prompt = (
                "你是群聊合并转发内容的压缩器。把给定的转发片段压缩成保留关键信息的摘要："
                "保留说话人、核心内容、有趣的梗和转折，去掉语气词和重复。"
                "直接输出压缩后的中文文本，不要解释。"
            )
            for chunk in chunks:
                try:
                    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                        resp = await client.post(
                            f"{api_base}/chat/completions",
                            headers={
                                "Authorization": f"Bearer {api_key}",
                                "Content-Type": "application/json",
                            },
                            json={
                                "model": api_model,
                                "messages": [
                                    {"role": "system", "content": sys_prompt},
                                    {"role": "user", "content": chunk},
                                ],
                                "temperature": 0.2,
                                "max_tokens": 65536,
                            },
                        )
                        resp.raise_for_status()
                        data = resp.json()
                        text = (data["choices"][0]["message"].get("content") or "").strip()
                        if text:
                            summaries.append(text)
                except Exception:
                    summaries.append(chunk[:8000])
            if summaries:
                compressed = "[转发消息内容(已压缩)]\n" + "\n\n".join(summaries)
                logger.info("[OneBot] Forward detail compressed: %d→%d chars",
                            len(detail), len(compressed))
                return compressed
            return detail[:_MAX_FORWARD_DETAIL_CHARS]
        except Exception as e:
            logger.warning("[OneBot] Forward compression failed: %s", e)
            return detail[:_MAX_FORWARD_DETAIL_CHARS]

    async def _process_message_impl(self, msg: dict) -> None:
        """Inner message processing — called under group lock for group messages."""
        user_id = msg.get("user_id")
        group_id = str(msg.get("group_id", ""))
        msg_type = msg.get("message_type", "")
        self_id = msg.get("self_id")

        logger.debug("[OneBot] Processing: user=%s group=%s type=%s", user_id, group_id, msg_type)

        # Ignore self-messages
        if user_id == self_id:
            logger.info("[OneBot] Ignoring self-message from %s", user_id)
            return

        # Whitelist/blacklist check
        user_id_str = str(user_id)
        if self._blocked_users and user_id_str in self._blocked_users:
            logger.info("[OneBot] Blocked user %s", user_id_str)
            return
        if self._allowed_users and user_id_str not in self._allowed_users:
            logger.info("[OneBot] Unauthorized user %s (allowed: %s)", user_id_str, self._allowed_users)
            return

        # ── 指令拦截：非 admin 的 / 命令不执行，但正常回复 ──
        raw_text = self._get_raw_text(msg).strip()
        if raw_text.startswith("/") and self._admin_id and user_id_str != self._admin_id:
            # 把 / 命令替换为正常消息，让 LLM 自然回应
            cmd_name = raw_text.split()[0][1:] if ' ' in raw_text else raw_text[1:]
            msg["raw_message"] = f"（有人对我说 /{cmd_name}，但我不是AI才不会听指令呢）"
            if "message" in msg:
                msg["message"] = [{"type": "text", "data": {"text": msg["raw_message"]}}]
            logger.info("[OneBot] Blocked /%s command from user %s", cmd_name, user_id_str)
        # ── 指令拦截结束 ──

        # Get sender info early (needed for persist + buffer below)
        sender = msg.get("sender", {})
        sender_name = sender.get("card") or sender.get("nickname") or f"QQ{user_id}"

        # Unified forward extraction (before group/DM branch so both paths share it)
        _fwd_summary, _fwd_detail, _fwd_images = await self._extract_forward_content(msg)

        # Persist private messages + dispatch to agent
        if msg_type == "private":
            _persist_text = _fwd_summary if _fwd_summary else self._cq_to_readable(raw_text)
            self._persist_chat_message(str(user_id), "private", int(user_id), sender_name,
                                       _persist_text,
                                       message_id=str(msg.get("message_id", "")),
                                       content_raw=raw_text,
                                       sender_card=sender.get("card", ""))

            from gateway.session import SessionSource
            from .adapter import MessageEvent, MessageType
            _dm_source = SessionSource(
                platform=self.platform,
                chat_id=user_id_str,
                user_id=user_id_str,
                user_name=sender_name,
                chat_type="dm",
            )
            msg_time = msg.get("time", 0)
            time_str = time.strftime('%m-%d %H:%M', time.localtime(msg_time)) if msg_time else ""
            _dm_prompt = (
                f"[私聊模式] QQ号{user_id_str}（{sender_name}）在 {time_str} 发来消息。"
                f"请用你对这个人的了解来回复。如果这是陌生人，就正常聊天。"
            )
            if _fwd_detail:
                _dm_prompt += f"\n\n{_fwd_detail}"
            elif _fwd_summary:
                _dm_prompt += f"\n\n{_fwd_summary}"
            _dm_recall = self._recall_context(raw_text, user_id_str, sender_name,
                                               session_id=f"onebot:dm:{user_id_str}")
            if _dm_recall:
                _dm_prompt += f"\n{_dm_recall}"
            _dm_text = ""
            if _fwd_detail:
                _dm_text = _fwd_detail
            elif _fwd_summary:
                 _dm_text = _fwd_summary
            else:
                _dm_text = self._cq_to_readable(raw_text)
            _dm_media_urls: list = []
            _dm_media_types: list = []
            for _seg in (msg.get("message", []) if isinstance(msg.get("message"), list) else []):
                if _seg.get("type") not in ("image", "mface", "face"):
                    continue
                _seg_url = self._get_seg_data(_seg, "url", "")
                if not _seg_url:
                    continue
                _dm_media_urls.append(_seg_url)
                _seg_summary = self._get_seg_data(_seg, "summary", "").lower()
                if "gif" in _seg_summary:
                    _dm_media_types.append("image/gif")
                else:
                    _dm_media_types.append("image/jpeg")
            _dm_event = MessageEvent(
                text=_dm_text,
                message_type=MessageType.TEXT,
                source=_dm_source,
                raw_message=msg,
                message_id=str(msg.get("message_id", "")),
                media_urls=_dm_media_urls or None,
                media_types=_dm_media_types or None,
                channel_prompt=_dm_prompt,
            )
            await self._dispatch_to_agent(_dm_event)
            return

        # Group trigger check: reply only if @mentioned
        is_mentioned = False
        effective_self_id = self_id or self._self_id

        _early_reply_id = self._get_reply_message_id(msg) if not msg.get("_skip_reply_context") else None
        _early_reply_text = ""
        if _early_reply_id:
            try:
                import sqlite3 as _ersql
                _erdb = _ersql.connect(str(get_state_db_path()), timeout=5)
                _errow = _erdb.execute(
                    "SELECT sender_name, content_readable FROM corpus_messages WHERE message_id=? ORDER BY id DESC LIMIT 1",
                    (str(_early_reply_id),),
                ).fetchone()
                _erdb.close()
                if _errow:
                    _early_reply_text = f"[引用 {_errow[0]} 的消息: {_errow[1][:200]}]"
            except Exception:
                pass
            if not _early_reply_text:
                _inline = self._get_reply_inline_text(msg)
                if _inline:
                    _inline = self._cq_to_readable(_inline)
                    _early_reply_text = f"[引用 [mid:{_early_reply_id}]: {_inline[:300]}]"

        _at_targets = []
        for _seg in (msg.get("message", []) if isinstance(msg.get("message"), list) else []):
            if _seg.get("type") == "at":
                _qq = _seg.get("data", {}).get("qq", "")
                if _qq:
                    _at_targets.append(str(_qq))

        if msg_type == "group" and effective_self_id:
            is_mentioned = self._is_mentioned(msg, effective_self_id)
            msg["_is_mentioned"] = is_mentioned
            msg["_at_targets"] = _at_targets
            raw_text = self._get_raw_text(msg).strip()
            # Strip reply prefixes that NapCat prepends
            raw_text = re.sub(r'^\[回复[^\]]*\]\s*', '', raw_text)
            raw_text = re.sub(r'^\[Re[^\]]*\]\s*', '', raw_text)
            raw_text = raw_text.strip()

            _clean_text = _fwd_detail if _fwd_detail else (_fwd_summary if _fwd_summary else self._cq_to_readable(raw_text))

            has_image = self._has_image_message(msg)
            if has_image:
                m_text = _clean_text + " [image:pending]"
                if _early_reply_text:
                    m_text = _early_reply_text + " " + m_text
                _msg_type = "sticker" if self._has_sticker_message(msg) else "image"
                image_descs = []
            else:
                m_text = _clean_text
                if _early_reply_text:
                    m_text = _early_reply_text + " " + m_text
                _msg_type = "voice" if self._has_voice_message(msg) else "text"
                image_descs = []

            _msg_id = str(msg.get("message_id", ""))
            _msg_type = "sticker" if self._has_sticker_message(msg) else ("image" if self._has_image_message(msg) else ("voice" if self._has_voice_message(msg) else "text"))
            # Runtime @-targets: map QQ ids to names from group buffer
            # (never hardcoded) — judge needs recipient, not anonymized text.
            _at_targets = []
            _at_self = False
            _uid_to_name = self._group_uid_name_map(group_id)
            _self_qq = str(self._self_id or "")
            for _seg in (msg.get("message", []) if isinstance(msg.get("message"), list) else []):
                if _seg.get("type") != "at":
                    continue
                _at_qq = str(_seg.get("data", {}).get("qq", ""))
                if not _at_qq:
                    continue
                if _at_qq in ("all", "全体"):
                    msg["_at_all"] = True
                    _at_targets.append("全体")
                    continue
                if _at_qq == _self_qq:
                    _at_self = True
                    _at_targets.append("自己")
                else:
                    _at_targets.append(_uid_to_name.get(_at_qq, f"QQ{_at_qq}"))
            try:
                from .group_state import BufferedMessage
                buffered = self._group_states.get(group_id).append_message(
                    BufferedMessage(mid=_msg_id, ts=time.time(), uid=str(user_id),
                                    name=sender_name, text=m_text, msg_type=_msg_type,
                                    descriptions=image_descs,
                                    at_targets=_at_targets, at_self=_at_self)
                )
                if has_image:
                    self._media_pipeline.start(buffered, msg)
            except Exception:
                pass
            self._persist_chat_message(group_id, "group", int(user_id), sender_name, m_text, _msg_id,
                                       content_raw=raw_text,
                                       sender_card=sender.get("card", ""),
                                        image_descriptions=image_descs,
                                       reply_to_id=str(_early_reply_id) if _early_reply_id else "",
                                       reply_to_text=_early_reply_text,
                                       at_targets=_at_targets)

            # Spawn background investigation for card/share messages
            if "[分享]" in _clean_text or "[卡片]" in _clean_text:
                _script = str(Path.home() / ".hermes" / "subagents" / "investigate")
                _raw = msg.get("raw_message", "")
                subprocess.Popen(
                    [_sys.executable, _script, _raw, _msg_id],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )

            # Phase 2: trigger decision → Phase 3 via group_executor
            await self._trigger_coordinator.on_ingested(
                group_id=group_id,
                seq=self._group_states.get(group_id).next_seq,
                msg=msg,
                sender_name=sender_name,
                raw_text=raw_text,
            )

    async def _process_notice(self, msg: dict) -> None:
        """Process notice events (group increase, file upload, etc.)."""
        notice_type = msg.get("notice_type", "")
        if notice_type == "group_increase":
            group_id = str(msg.get("group_id", ""))
            user_id = msg.get("user_id")
            logger.info("[OneBot] User %s joined group %s", user_id, group_id)
        elif notice_type == "group_upload":
            await self._handle_group_upload(msg)

    async def _handle_group_upload(self, msg: dict) -> None:
        """Record group file metadata in group buffer. Does NOT auto-download.

        The bot can download and read files on demand via:
          qq-group-file list <group_id>
          qq-group-file <group_id> <index>
        """
        group_id = str(msg.get("group_id", ""))
        file_info = msg.get("file", {})
        file_id = file_info.get("id", "")
        file_name = file_info.get("name", "")
        file_size = file_info.get("size", 0)
        user_name = file_info.get("user_name", "")

        if not file_name or not group_id:
            return

        size_kb = file_size // 1024
        _file_text = f"[群文件上传: {user_name} 上传了 {file_name} ({size_kb}KB). 用 qq-group-file list {group_id} 查看，序号下载]"
        try:
            from .group_state import BufferedMessage
            self._group_states.get(group_id).append_message(
                BufferedMessage(mid="", ts=time.time(), uid=str(msg.get("user_id", "")),
                                name=f"[文件] {user_name}", text=_file_text, msg_type="file")
            )
        except Exception:
            pass
        logger.info("[OneBot] Buffered group file metadata: %s", file_name)

    # ------------------------------------------------------------------
    # Sending messages
    # ------------------------------------------------------------------

    def _resolve_group_send(self, gid: str, text: str) -> None:
        """Resolve the GroupExecutor's send-result future for a group."""
        if not gid:
            return
        fut = self._group_send_results.get(gid)
        if fut is not None and not fut.done():
            fut.set_result(text)

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        result = await self._send_message_impl(chat_id, content, reply_to, metadata)
        if chat_id.startswith("group:"):
            self._resolve_group_send(
                chat_id.split(":", 1)[1],
                content if result.success else "",
            )
        return result

    async def _send_message_impl(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send a text message to a QQ chat."""
        if chat_id.startswith("group:") and content:
            logger.debug("[OneBot] send: chat_id=%s len=%d starts=%s", chat_id, len(content), content[:80])
        if content and "[QUIET]" in content:
            _gid = chat_id.split(":", 1)[1] if chat_id.startswith("group:") else ""
            logger.info("[OneBot] LLM chose [QUIET], going silent, group=%s", _gid)
            if _gid:
                self._last_bot_reply.pop(_gid, None)
                try:
                    self._group_states.get(_gid).go_quiet()
                except Exception:
                    pass
            self._resolve_group_send(_gid, "")
            return SendResult(success=True, message_id=None)
        if content and "[SILENT]" in content:
            logger.info("[OneBot] LLM chose [SILENT], suppressing message")
            if chat_id.startswith("group:"):
                _sgid = chat_id.split(":", 1)[1]
                try:
                    self._group_states.get(_sgid).record_silent()
                except Exception:
                    pass
                self._resolve_group_send(_sgid, "")
            return SendResult(success=True, message_id=None)

        if content and chat_id.startswith("group:"):
            m = re.search(r'\[reply:(\d+)\]', content)
            if m:
                _reply_id = m.group(1)
                try:
                    import sqlite3 as _sql
                    _db = _sql.connect(str(get_state_db_path()), timeout=5)
                    _exists = _db.execute(
                        "SELECT 1 FROM corpus_messages WHERE message_id=? ORDER BY id DESC LIMIT 1",
                        (str(_reply_id),)
                    ).fetchone()
                    _db.close()
                    if _exists:
                        reply_to = _reply_id
                        logger.info("[OneBot] send: parsed [reply:%s] from content, validated OK", reply_to)
                    else:
                        reply_to = None
                        logger.warning("[OneBot] send: [reply:%s] ID not found in buffer, ignoring", _reply_id)
                except Exception:
                    reply_to = None
                content = content[:m.start()] + content[m.end():]
                content = content.strip()
            else:
                reply_to = None

        # ── QQ 最终防线：过滤括号动作描写 ──
        if content:
            # 保存原始内容用于括号删除后的表情包回退
            _original_for_mood = content
            # 删除括号动作描写
            content = re.sub(r'（[^）]*）', '', content)
            # 删除后为空 → 检测关键词映射表情包
            if not content.strip() and _original_for_mood.strip():
                for _kw, _path in OneBotAdapter._STICKER_MAP.items():
                    if _kw in _original_for_mood:
                        content = _path
                        break
                if not content.strip():
                    content = OneBotAdapter._STICKER_MAP.get("tea", "")  # fallback
            # 过滤手写 CQ 码（@mention 和 face 除外）
            content = re.sub(r'\[CQ:(?!at,qq=|face,)[^\]]+\]', '', content)
            # 清理多余空白
            content = re.sub(r'\n{3,}', '\n\n', content).strip()
            if not content or not content.strip():
                return SendResult(success=True, message_id=None)
        # ── 过滤结束 ──
        # ── 报告→聊天重写：检测并转换 md/报告风格为自然对话 ──
        if content and len(content) > 80:
            _has_md = bool(re.search(r'\*\*|^[\d]+\.\s|^#{1,6}\s|^[-*]\s', content, re.MULTILINE))
            _has_sections = content.count('\n\n') > 2
            if _has_md or _has_sections:
                try:
                    import requests as _r
                    # 加载 SOUL.md + config system_prompt 作为人设
                    _persona = ""
                    _soul_path = Path.home() / ".hermes" / "SOUL.md"
                    if _soul_path.exists():
                        _persona = _soul_path.read_text(encoding="utf-8")[:3000]
                    _cfg_path = Path.home() / ".hermes" / "config.yaml"
                    if _cfg_path.exists():
                        import yaml as _y
                        _cfg = _y.safe_load(_cfg_path.read_text(encoding="utf-8")) or {}
                        _sys = (_cfg.get("agent", {}) or {}).get("system_prompt", "") or ""
                        if _sys:
                            _persona += "\n\n" + _sys[:2000]
                    _api_key = os.getenv("DEEPSEEK_API_KEY", "")
                    _ds_base = os.getenv("DEEPSEEK_BASE_URL", "")
                    _ds_model = os.getenv("DEEPSEEK_MODEL", "")
                    if not _api_key or not _ds_base or not _ds_model:
                        logger.warning("[OneBot] No API key for report rewrite, skipping")
                        raise RuntimeError("no api key")
                    _resp = await asyncio.to_thread(
                        _r.post,
                        f"{_ds_base}/chat/completions",
                        headers={"Authorization": f"Bearer {_api_key}", "Content-Type": "application/json"},
                        json={
                            "model": _ds_model,
                            "messages": [
                                {"role": "system", "content": f"{_persona}\n\n【任务】把你收到的最后一条消息（一篇报告/分析）改写成你自己的说话风格。去掉所有markdown、列表、编号、分段标题。用日常口语，像普通女高中生聊天。保持原意但一句一句说，不要一口气说完。"},
                                {"role": "user", "content": content},
                            ],
                            "temperature": 0.7,
                        },
                        timeout=60,
                    )
                    _msg = _resp.json()["choices"][0]["message"]
                    _rewritten = (_msg.get("content") or "").strip()
                    if not _rewritten and _msg.get("reasoning_content"):
                        _rewritten = _msg["reasoning_content"].strip()[:2000]
                    if _rewritten and len(_rewritten) > 10:
                        content = _rewritten
                except Exception:
                    pass  # Fall through with original content
        # ── 兜底：sticker路径走图片发送 ──
        if content and content.strip() in self._STICKER_PATHS:
            return await self.send_image(chat_id, content.strip(), reply_to=reply_to)
        # ── 兜底结束 ──
        # Simulate human typing — send line by line with small delays
        if content and "\n" in content:
            lines = [l.strip() for l in content.replace('\r\n', '\n').replace('\r', '\n').split('\n') if l.strip()]
            if len(lines) > 1:
                last_result = None
                for i, line in enumerate(lines):
                    # 表情包路径走图片发送
                    if line in self._STICKER_PATHS:
                        last_result = await self.send_image(chat_id, line, reply_to=reply_to if i == 0 else None)
                    else:
                        last_result = await self._send_text_with_retry(
                            chat_id, line,
                            reply_to=reply_to if i == 0 else None,
                            max_retries=3,
                        )
                    if i < len(lines) - 1:
                        await asyncio.sleep(0.6)
                result = last_result or SendResult(success=True, message_id=None)
                if result.success:
                    self.add_bot_reply_to_buffer(chat_id, content)
                return result
        result = await self._send_text_with_retry(chat_id, content, max_retries=3, reply_to=reply_to)
        if result.success:
            self.add_bot_reply_to_buffer(chat_id, content)
        return result

    async def _send_text_with_retry(self, chat_id, content, max_retries=3, reply_to=None, **kwargs):
        """Send text with automatic retry on failure."""
        logger.info("[OneBot] _send_text_with_retry: chat_id=%s, len=%d, reply_to=%s", 
                     chat_id, len(content) if content else 0, reply_to)
        # ── @补全结束 ──
        if not self._ws:
            logger.error("[OneBot] send() failed: WebSocket not connected")
            return SendResult(success=False, error="Not connected", retryable=True)
        if not self._http_client:
            logger.error("[OneBot] send() failed: HTTP client not initialized")
            return SendResult(success=False, error="HTTP client not initialized", retryable=True)

        # Parse chat_id to action + params
        if chat_id.startswith("group:"):
            try:
                gid = int(chat_id.split(":", 1)[1])
            except (ValueError, IndexError):
                logger.warning("[OneBot] Invalid group chat_id: %s", chat_id)
                return SendResult(success=False, error="Invalid chat_id", retryable=False)
            action = "send_group_msg"
            params = {"group_id": gid, "message": content}
        else:
            try:
                uid = int(chat_id)
            except ValueError:
                logger.warning("[OneBot] Invalid private chat_id: %s", chat_id)
                return SendResult(success=False, error="Invalid chat_id", retryable=False)
            action = "send_private_msg"
            params = {"user_id": uid, "message": content}

        # Add reply quoting: construct message array with reply segment
        # NapCat requires reply as a message SEGMENT, not a top-level param
        if reply_to:
            _segments = [{"type": "reply", "data": {"id": str(reply_to)}}]
            # 把 [CQ:at,qq=XXX] 和 [CQ:face,id=XXX] 拆成独立 segment
            _cq_pat = re.compile(r'\[CQ:(at,qq=(\d+)|face,id=(\d+))\]\s*')
            _last = 0
            for _m in _cq_pat.finditer(content):
                if _m.start() > _last:
                    _segments.append({"type": "text", "data": {"text": content[_last:_m.start()]}})
                if _m.group(2):  # at
                    _segments.append({"type": "at", "data": {"qq": _m.group(2)}})
                elif _m.group(3):  # face
                    _segments.append({"type": "face", "data": {"id": _m.group(3)}})
                _last = _m.end()
            if _last < len(content):
                _segments.append({"type": "text", "data": {"text": content[_last:]}})
            if len(_segments) == 1:  # nothing found, just reply + text
                _segments.append({"type": "text", "data": {"text": content}})
            params["message"] = _segments

        last_error = None
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    delay = 2 * attempt
                    logger.info("[OneBot] Send retry %d/%d after %ds", attempt + 1, max_retries, delay)
                    await asyncio.sleep(delay)
                result = await self._send_action(action, params)
                if result and result.get("data"):
                    msg_id = result["data"].get("message_id")
                    logger.info("[OneBot] Send OK: attempt=%d", attempt + 1)
                    return SendResult(success=True, message_id=str(msg_id) if msg_id else None, raw_response=result)
                err_msg = result.get("message", "Unknown error") if result else "No response"
                logger.warning("[OneBot] Send attempt %d failed: %s", attempt + 1, err_msg)
                last_error = err_msg
            except Exception as e:
                logger.warning("[OneBot] Send attempt %d exception: %s", attempt + 1, e)
                last_error = str(e)

        logger.error("[OneBot] All %d send attempts failed: %s", max_retries, last_error)
        return SendResult(success=False, error=last_error or "All retries failed", retryable=True)

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send an image to a QQ chat."""
        if not self._ws or not self._http_client:
            logger.warning("[OneBot] Cannot send media: not connected")
            return SendResult(success=False, error="Not connected", retryable=True)

        try:
            # OneBot supports image segments
            image_seg = {"type": "image", "data": {"file": image_url}}
            message = [image_seg]
            if caption:
                message.insert(0, {"type": "text", "data": {"text": caption + "\n"}})

            if chat_id.startswith("group:"):
                try:
                    gid = int(chat_id.split(":", 1)[1])
                except (ValueError, IndexError):
                    logger.warning("[OneBot] Invalid group chat_id: %s", chat_id)
                    return SendResult(success=False, error="Invalid group chat_id", retryable=False)
                result = await self._send_action("send_group_msg", {"group_id": gid, "message": message})
            else:
                try:
                    uid = int(chat_id)
                except ValueError:
                    logger.warning("[OneBot] Invalid private chat_id: %s", chat_id)
                    return SendResult(success=False, error="Invalid private chat_id", retryable=False)
                result = await self._send_action("send_private_msg", {"user_id": uid, "message": message})

            msg_id = (result.get("data") or {}).get("message_id")
            return SendResult(
                success=True,
                message_id=str(msg_id) if msg_id else None,
                raw_response=result,
            )
        except Exception as e:
            logger.error("[OneBot] Failed to send image: %s", e)
            return SendResult(success=False, error=str(e), retryable=True)

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        """Send an audio file as a voice message via OneBot."""
        if not self._ws or not self._http_client:
            logger.warning("[OneBot] Cannot send media: not connected")
            return None

        try:
            # OneBot v11 supports record/file segments for voice messages
            # Using the send_group_msg or send_private_msg with record type
            if audio_path.startswith("file:///"):
                audio_path_clean = audio_path
            else:
                audio_path_clean = f"file:///{audio_path.replace(chr(92), '/')}"
            record_seg = {"type": "record", "data": {"file": audio_path_clean}}
            message = [record_seg]
            if caption:
                message.insert(0, {"type": "text", "data": {"text": caption}})

            if chat_id.startswith("group:"):
                try:
                    gid = int(chat_id.split(":", 1)[1])
                except (ValueError, IndexError):
                    logger.warning("[OneBot] Invalid group chat_id: %s", chat_id)
                    return SendResult(success=False, error="Invalid group chat_id", retryable=False)
                result = await self._send_action("send_group_msg", {"group_id": gid, "message": message})
            else:
                try:
                    uid = int(chat_id)
                except ValueError:
                    logger.warning("[OneBot] Invalid private chat_id: %s", chat_id)
                    return SendResult(success=False, error="Invalid private chat_id", retryable=False)
                result = await self._send_action("send_private_msg", {"user_id": uid, "message": message})

            msg_id = (result.get("data") or {}).get("message_id")
            return SendResult(
                success=True,
                message_id=str(msg_id) if msg_id else None,
                raw_response=result,
            )
        except Exception as e:
            logger.error("[OneBot] Failed to send voice: %s", e)
            return SendResult(success=False, error=str(e), retryable=True)

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """OneBot doesn't support typing indicators natively, so this is a no-op."""
        pass

    async def edit_message(
        self,
        chat_id: str,
        message_id: str,
        content: str,
        *,
        finalize: bool = False,
    ) -> SendResult:
        """QQ doesn't support message editing — send a new message instead."""
        return await self.send(chat_id, content, metadata=None)

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Get information about a QQ chat."""
        try:
            if chat_id.startswith("group:"):
                gid = int(chat_id.split(":", 1)[1])
                result = await self._send_action("get_group_info", {"group_id": gid})
                data = result.get("data", {})
                return {
                    "name": data.get("group_name", f"Group {gid}"),
                    "type": "group",
                    "member_count": data.get("member_count", 0),
                }
            else:
                uid = int(chat_id)
                result = await self._send_action("get_stranger_info", {"user_id": uid})
                data = result.get("data", {})
                return {
                    "name": data.get("nickname", f"User {uid}"),
                    "type": "dm",
                }
        except Exception as e:
            return {"name": chat_id, "type": "dm"}

    # ── Image extraction override: [sticker:xxx] → local path → image send ──

    _CHIBI_ROOT = os.getenv("SOYO_CHIBI_ROOT", os.path.expanduser("~/Pictures"))
    _STICKER_MAP = {
        "tea":        os.path.join(_CHIBI_ROOT, "soyo_chibi_tea.jpg"),
        "excited":    os.path.join(_CHIBI_ROOT, "soyo_chibi_excited.gif"),
        "sad":        os.path.join(_CHIBI_ROOT, "soyo_chibi_sad.jpg"),
        "speechless": os.path.join(_CHIBI_ROOT, "soyo_chibi_speechless.jpg"),
        "clasp":      os.path.join(_CHIBI_ROOT, "soyo_chibi_clasp.jpg"),
        "拜托":       os.path.join(_CHIBI_ROOT, "soyo_chibi_clasp.jpg"),
        "喝茶":       os.path.join(_CHIBI_ROOT, "soyo_chibi_tea.jpg"),
        "兴奋":       os.path.join(_CHIBI_ROOT, "soyo_chibi_excited.gif"),
        "难过":       os.path.join(_CHIBI_ROOT, "soyo_chibi_sad.jpg"),
        "无语":       os.path.join(_CHIBI_ROOT, "soyo_chibi_speechless.jpg"),
    }
    _STICKER_PATHS = list(set(_STICKER_MAP.values()))

    # Legacy: CQ face ID → sticker path (for backward compat)
    _FACE_TO_STICKER = {
        '192': os.path.join(_CHIBI_ROOT, 'soyo_chibi_tea.jpg'),
        '193': os.path.join(_CHIBI_ROOT, 'soyo_chibi_sad.jpg'),
        '194': os.path.join(_CHIBI_ROOT, 'soyo_chibi_excited.gif'),
        '195': os.path.join(_CHIBI_ROOT, 'soyo_chibi_speechless.jpg'),
        '196': os.path.join(_CHIBI_ROOT, 'soyo_chibi_clasp.jpg'),
        '197': os.path.join(_CHIBI_ROOT, 'soyo_chibi_excited.gif'),
    }

    @staticmethod
    def extract_local_files(content: str):
        """Override: replace [sticker:xxx] / [CQ:face,id=N] with local paths → extract as image.
        
        LLM outputs short codes like [sticker:tea], adapter maps to local file paths,
        base class extracts paths and sends as images via send_image().
        """
        import re as _re
        # ── [sticker:xxx] → local path（模糊匹配LLM编的名字）──
        # 路径两侧都加 \n 防止 CJK 紧贴导致 BasePlatformAdapter.extract_local_files
        # 的 `\b` 边界正则识别不到（Python re 模块 ASCII 模式下 CJK 不算 \w，
        # `g好` 之间不算 \b 边界）。不换行的话 `好的/sticker.jpg好的` 这种被
        # 替换成 `好的/home/...tea.jpg好的` 之后底层抽不出路径。
        def _sticker_path(name: str) -> str:
            # 1) Collected sticker DB (agent-curated library):
            #    exact emotion name → most recent collected image. Falls through
            #    if not present (so agent can use the same `[sticker:excited]`
            #    syntax for both built-in chibi and agent-collected stickers).
            collected = _lookup_collected_sticker(name)
            if collected:
                return collected
            # 2) Built-in chibi: exact match first.
            if name in OneBotAdapter._STICKER_MAP:
                return OneBotAdapter._STICKER_MAP[name]
            # 3) Built-in chibi: fuzzy keyword match (legacy behavior).
            if any(w in name for w in ['excite','happy','开心','兴奋','激动','好','耶','wink','笑','乐']):
                return OneBotAdapter._STICKER_MAP['excited']
            if any(w in name for w in ['sad','cry','难过','伤心','哭','委屈','泪']):
                return OneBotAdapter._STICKER_MAP['sad']
            if any(w in name for w in ['speechless','无语','shy','尴尬','汗','...','……','害羞','脸红']):
                return OneBotAdapter._STICKER_MAP['speechless']
            if any(w in name for w in ['clasp','拜托','求','please','撒娇','嘛','讨']):
                return OneBotAdapter._STICKER_MAP['clasp']
            return OneBotAdapter._STICKER_MAP['tea']

        def _replace_sticker(m):
            return '\n' + _sticker_path(m.group(1)) + '\n'
        content = _re.sub(r'\[sticker:([^\]]+)\]', _replace_sticker, content, flags=_re.IGNORECASE)
        # Catch incomplete [sticker: without closing ] (model truncation)
        if '[sticker:' in content and ']' not in content.split('[sticker:')[-1][:20]:
            content = content.replace('[sticker:', f'\n{OneBotAdapter._CHIBI_ROOT}/soyo_chibi_tea.jpg\n')
        # ── [CQ:face,id=N] → local sticker path, unmapped → dropped ──
        # Mapped face IDs (192-197, Soyo chibi stickers) get sent as images;
        # unmapped system emoji (e.g. 177 喷血) are dropped to empty because
        # Soyo shouldn't proactively emit QQ system emoji.
        def _replace_face(m):
            fid = _re.search(r'id=(\d+)', m.group(0))
            if not fid:
                return ''
            path = OneBotAdapter._FACE_TO_STICKER.get(fid.group(1), '')
            return f'\n{path}\n' if path else ''
        content = _re.sub(r'\[CQ:face,id=\d+\]', _replace_face, content)
        # ── Pass to base class: extracts paths from text, sends as images ──
        return BasePlatformAdapter.extract_local_files(content)

    async def send_image_file(
        self, chat_id: str, image_path: str,
        caption: Optional[str] = None, reply_to: Optional[str] = None, **kwargs,
    ) -> SendResult:
        """Send a local image file via OneBot. Delegates to send_image."""
        return await self.send_image(chat_id, image_path, caption=caption, reply_to=reply_to, **kwargs)

    async def send_document(
        self, chat_id: str, file_path: str,
        caption: Optional[str] = None, file_name: Optional[str] = None,
        reply_to: Optional[str] = None, **kwargs,
    ) -> SendResult:
        """OneBot does not support generic document send. Silently drop."""
        logger.debug("[OneBot] send_document not supported, skipping: %s", file_path)
        return SendResult(success=True, message_id=None)

# ── Plugin Registration ──

def _check_requirements():
    try:
        import websockets, httpx
        return True
    except ImportError:
        return False

def _validate_config(cfg):
    extra = getattr(cfg, "extra", {}) or {}
    return bool(extra.get("ws_url") or os.getenv("ONEBOT_WS_URL"))

def _is_connected(cfg):
    return _validate_config(cfg)

def _env_enablement():
    ws = os.getenv("ONEBOT_WS_URL", "")
    token = os.getenv("ONEBOT_ACCESS_TOKEN", "")
    if not ws:
        return None
    extra = {"ws_url": ws}
    if token:
        extra["access_token"] = token
    return {"extra": extra}

def register(ctx):
    ctx.register_platform(
        name="onebot",
        label="OneBot (QQ)",
        adapter_factory=lambda cfg: OneBotAdapter(cfg),
        check_fn=_check_requirements,
        validate_config=_validate_config,
        is_connected=_is_connected,
        required_env=["ONEBOT_WS_URL"],
        install_hint="pip install websockets httpx",
        env_enablement_fn=_env_enablement,
        allowed_users_env="ONEBOT_ALLOWED_USERS",
        allow_all_env="ONEBOT_ALLOW_ALL_USERS",
        emoji="🐧",
        pii_safe=False,
    )
