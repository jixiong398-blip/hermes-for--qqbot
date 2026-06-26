"""
         ┌─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┐
         │      清   尘   璃   落      │
         └─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─┘
    上联：代码永无 bug  佛祖座下莲花放
    下联：{{CHANNEL_NAME}}赐福  素世心中万世安

              _ooOoo_
             o8888888o
             88" . "88
             (| -_- |)
             O\  =  /O
          ____/`---'\____
        .'  \\|     |//  `.
       /  \\|||  :  |||//  \
      /  _||||| -:- |||||-  \
      |   | \\\  -  /// |   |
      | \_|  ''\---/''  |   |
      \  .-\__  `-`  ___/-. /
    ___`. .'  /--.--\  `. . __
  ."" '<  `.___\_<|>_/___.'  >'"".
 | | :  `- \`.;`\ _ /`;.`/ - ` : | |
 \  \ `-.   \_ __\ /__ _/   .-` /  /
======`-.____`-.___\_____/___.-`____.-'======
                   `=---='
         }  }  }  }  莲花台  {  {  {  {
         }  }  }  }  莲花台  {  {  {  {
         }  }  }  }  莲花台  {  {  {  {
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
              佛祖保佑    永无 bug

OneBot v11 adapter for Hermes Agent.

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


class OneBotAdapter(BasePlatformAdapter):
    """OneBot v11 adapter for QQ (NapCat/Lagrange/go-cqhttp)."""

    # QQ does not support message editing, so streaming (which relies on edits)
    # must be disabled. The gateway will fall back to sending the full response
    # as a single message.
    SUPPORTS_MESSAGE_EDITING = False

    def __init__(self, config, **kwargs):
        from gateway.config import Platform as _Platform
        super().__init__(config=config, platform=_Platform("onebot"))
        self._ws = None
        self._ws_task: Optional[asyncio.Task] = None
        self._http_client = None
        self._echo_counter = 0
        self._pending_echo: Dict[str, asyncio.Future] = {}
        self._self_id: Optional[int] = None
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

        # Message dedup: prevent processing the same message twice
        self._seen_msg_ids: Dict[str, float] = {}  # msg_id → seen_at timestamp
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
                        db.execute("PRAGMA journal_mode=DELETE")
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
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                    except Exception:
                        if attempt < 2:
                            await asyncio.sleep(1)
                        db.close() if 'db' in dir() else None
                self._persist_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception:
                pass

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
                        await self._process_message(payload)

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
        if not isinstance(segments, list):
            return False
        self_str = str(self_id)
        for seg in segments:
            if seg.get("type") == "at":
                qq = seg.get("data", {}).get("qq") or seg.get("data", {}).get("id")
                if str(qq) == self_str:
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
        text = re.sub(r'\[CQ:face,[^\]]*\]', '[动画表情]', text)
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
        return any(seg.get("type") in ("image", "face", "mface") for seg in segments)

    def _has_sticker_message(self, msg: dict) -> bool:
        segments = msg.get("message", [])
        if not isinstance(segments, list):
            return False
        return any(seg.get("type") == "mface" for seg in segments)

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

    async def _describe_image(self, image_path: str) -> str:
        """Describe an image using the vision model (MiMo v2.5). Cached per path."""
        if image_path in self._image_descriptions:
            return self._image_descriptions[image_path]
        if not os.path.exists(image_path):
            self._image_descriptions[image_path] = "图片"
            return "图片"

        try:
            api_key = os.getenv("XIAOMI_API_KEY", "")
            api_base = os.getenv("XIAOMI_BASE_URL", "https://api.xiaomimimo.com/v1")
            api_model = os.getenv("XIAOMI_MODEL", "mimo-v2.5")
            if not api_key:
                self._image_descriptions[image_path] = "图片"
                return "图片"

            import base64
            with open(image_path, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode()
            ext = os.path.splitext(image_path)[1].lower()
            mime = self._mime_for_ext(ext)

            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
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
                                {"type": "text", "text": "简洁描述这张图片内容，包括文字、表情、动作。中文，不超过40字。"},
                                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                            ],
                        }],
                        "max_tokens": 80,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                desc = data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning("[OneBot] Image description failed for %s: %s", image_path, e)
            desc = "图片"

        if len(self._image_descriptions) > 500:
            _oldest = next(iter(self._image_descriptions))
            del self._image_descriptions[_oldest]
        self._image_descriptions[image_path] = desc
        return desc

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
            if _seg_type not in ("image", "face", "mface"):
                continue

            file_url = self._get_seg_data(seg, "url", "")
            file_id = self._get_seg_data(seg, "file", "")
            # QQ system emoji (type=face): no downloadable file, just an id.
            # Annotate for the AI instead of failing silently.
            if _seg_type == "face":
                face_id = self._get_seg_data(seg, "id", "")
                if face_id:
                    # Store as a pseudo-path so [image:...] hint still works
                    paths.append(f"qq_face:{face_id}")
                    logger.info("[OneBot] QQ face emoji detected: id=%s", face_id)
                continue
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
        msg["raw_message"] = f"[CQ:at,qq={{BOT_QQ_ID}}] {merged_text}"
        msg["message"] = [
            {"type": "at", "data": {"qq": "{{BOT_QQ_ID}}"}},
            {"type": "text", "data": {"text": f"[合并消息，{len(entries)}人@]: {merged_text}"}}
        ]
        # Re-process without batching — include images in merged message
        merged_msg_arr = [
            {"type": "at", "data": {"qq": "{{BOT_QQ_ID}}"}},
            {"type": "text", "data": {"text": f"[合并消息，{len(entries)}人@]: {merged_text}"}}
        ]
        # Attach original image/face/mface segments so _get_image_files can process them
        for e in entries:
            for seg in e.get("msg", {}).get("message", []):
                if isinstance(seg, dict) and seg.get("type") in ("image", "face", "mface"):
                    merged_msg_arr.append(seg)
        msg["message"] = merged_msg_arr
        msg["_skip_mention_batch"] = True
        msg["_skip_dedup"] = True
        await self._process_message(msg)

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

        if self_id:
            self._self_id = self_id

        # Per-group lock: serialize message processing within a group
        # Prevents concurrent agent runs from context interference / truncation
        if msg_type == "group" and group_id:
            group_lock = self._get_group_lock(str(group_id))
            async with group_lock:
                return await self._process_message_impl(msg)
        else:
            return await self._process_message_impl(msg)

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

        # ── 指令拦截：非 {{HOME_CHANNEL}} 的 / 命令不执行，但正常回复 ──
        raw_text = self._get_raw_text(msg).strip()
        if raw_text.startswith("/") and user_id_str != "{{HOME_CHANNEL}}":
            # 把 / 命令替换为正常消息，让 LLM 自然回应
            cmd_name = raw_text.split()[0][1:] if ' ' in raw_text else raw_text[1:]
            msg["raw_message"] = f"（有人对我说 /{cmd_name}，但我不是AI才不会听指令呢）"
            if "message" in msg:
                msg["message"] = [{"type": "text", "data": {"text": msg["raw_message"]}}]
            logger.info("[OneBot] Blocked /%s command from user %s", cmd_name, user_id_str)
        # ── 指令拦截结束 ──

        # Group trigger check: reply only if @mentioned
        is_mentioned = False
        effective_self_id = self_id or self._self_id
        # Get sender info early (needed for buffer below)
        sender = msg.get("sender", {})
        sender_name = sender.get("card") or sender.get("nickname") or f"QQ{user_id}"

        _early_reply_id = self._get_reply_message_id(msg) if not msg.get("_skip_reply_context") else None
        _early_reply_text = ""
        if _early_reply_id:
            try:
                import sqlite3 as _ersql
                _erdb = _ersql.connect(str(get_state_db_path()), timeout=5)
                _errow = _erdb.execute(
                    "SELECT sender_name, content_readable FROM corpus_messages WHERE message_id=? LIMIT 1",
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
            raw_text = self._get_raw_text(msg).strip()
            # Strip reply prefixes that NapCat prepends
            raw_text = re.sub(r'^\[回复[^\]]*\]\s*', '', raw_text)
            raw_text = re.sub(r'^\[Re[^\]]*\]\s*', '', raw_text)
            raw_text = raw_text.strip()

            # Pre-download images in group messages for context (even if lurking)
            _image_hint = ""
            _image_descs = []
            if self._has_image_message(msg):
                try:
                    _img_paths = await self._get_image_files(msg)
                    if _img_paths:
                        _image_hint = " [image:" + ",".join(_img_paths) + "]"
                        _is_sticker = self._has_sticker_message(msg)
                        for _ip in _img_paths[:5]:
                            for _p in _ip.split(","):
                                _p = _p.strip()
                                if _p and _p != "download_failed":
                                    try:
                                        _d = await self._describe_image(_p)
                                        _tag = "表情包" if _is_sticker else "图片"
                                        _image_descs.append(f"[{_tag}: {_d}]")
                                    except Exception:
                                        pass
                    else:
                        _image_hint = " [image:download_failed]"
                except Exception:
                    _image_hint = " [image:download_failed]"

            # Buffer ALL group messages for context
            # Extract forwarded/merged chat records from segment data BEFORE cleaning
            _fwd_text = ""
            _segments = msg.get("message", [])
            _seg_types = [s.get("type","?") for s in _segments] if isinstance(_segments, list) else []
            if "forward" in _seg_types or "node" in _seg_types or "CQ:forward" in (raw_text or ""):
                try:
                    _full = await self._send_action("get_forward_msg", {"message_id": msg.get("message_id")})
                    _segments = (_full.get("data", {}) or {}).get("messages", [])
                except Exception as e:
                    logger.debug("[OneBot] get_forward_msg failed: %s", e)
            # Build forward text from either segment data.content or API messages
            _fwd_parts = []
            _fwd_image_paths = []
            for _seg in _segments if isinstance(_segments, list) else []:
                if _seg.get("sender"):  # API response
                    _name = _seg.get("sender", {}).get("nickname", "?")
                    _t = _seg.get("raw_message") or OneBotAdapter._get_text_from_segments(_seg)
                    if self._has_image_message(_seg):
                        try:
                            _imgs = await self._get_image_files(_seg)
                            if _imgs:
                                _fwd_image_paths.extend(_imgs)
                                _t = (_t or "") + " [图片]"
                        except Exception:
                            _t = (_t or "") + " [图片]"
                    _uid = _seg.get("user_id") or _seg.get("sender", {}).get("user_id", "")
                    _prefix = f"{_name}(QQ{_uid})" if _uid else _name
                    _fwd_parts.append(f"{_prefix}: {_t[:80]}")
                elif _seg.get("type") in ("forward", "node"):
                    _content = _seg.get("data", {}).get("content")
                    if isinstance(_content, list):
                        for _fm in _content[:10]:
                            _name = _fm.get("sender", {}).get("nickname", "?")
                            _t = _fm.get("raw_message") or OneBotAdapter._get_text_from_segments(_fm)
                            if self._has_image_message(_fm):
                                try:
                                    _imgs = await self._get_image_files(_fm)
                                    if _imgs:
                                        _fwd_image_paths.extend(_imgs)
                                        _t = (_t or "") + " [图片]"
                                except Exception:
                                    _t = (_t or "") + " [图片]"
                            _uid = _fm.get("user_id") or _fm.get("sender", {}).get("user_id", "")
                            _prefix = f"{_name}(QQ{_uid})" if _uid else _name
                            _fwd_parts.append(f"{_prefix}: {_t[:80]}")
                    break
            if _fwd_parts:
                _fwd_text = "[转发: " + " | ".join(_fwd_parts) + "]"
                logger.debug("[OneBot] Extracted forward: %d msgs, %d images", len(_fwd_parts), len(_fwd_image_paths))
            # Clean CQ codes — use forward text if available, otherwise raw text
            _clean_text = _fwd_text if _fwd_text else self._cq_to_readable(raw_text)
            # Inject downloaded forward images into buffer so vision tool can process them
            if _fwd_image_paths:
                _clean_text += " [image:" + ",".join(_fwd_image_paths) + "]"
            m_text = (_clean_text + _image_hint)
            if _image_descs:
                m_text += " " + " ".join(_image_descs)
            if _early_reply_text:
                m_text = _early_reply_text + " " + m_text
            _msg_id = str(msg.get("message_id", ""))
            _msg_type = "sticker" if self._has_sticker_message(msg) else ("image" if self._has_image_message(msg) else ("voice" if self._has_voice_message(msg) else "text"))
            try:
                from .group_state import BufferedMessage
                self._group_states.get(group_id).append_message(
                    BufferedMessage(mid=_msg_id, ts=time.time(), uid=str(user_id),
                                    name=sender_name, text=m_text, msg_type=_msg_type,
                                    descriptions=_image_descs)
                )
            except Exception:
                pass
            self._persist_chat_message(group_id, "group", int(user_id), sender_name, m_text, _msg_id,
                                       content_raw=raw_text,
                                       sender_card=sender.get("card", ""),
                                       image_descriptions=_image_descs,
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

            should_trigger = False
            decision_reason = ""
            _judge_result = None
            if self._has_voice_message(msg):
                _preview_text = "[语音消息]"
            elif self._has_image_message(msg):
                _preview_text = "[图片消息]"
            else:
                _preview_text = self._cq_to_readable(self._get_raw_text(msg) or "")
            try:
                from .semantic_judge import semantic_judge as _sj
                _gs = self._group_states.get(group_id)
                _attentive = _gs.is_attentive()
                _att_state_str = "对话态" if _attentive else ("旁观态" if _gs.is_episode_active() else "潜水")
                _last_reply_text = _gs.last_reply[1] if _gs.last_reply else ""
                _mins = (time.time() - _gs.last_reply[0]) / 60.0 if _gs.last_reply else 0.0
                _ep_dur = (time.time() - _gs.episode_start) / 60.0 if _gs.episode_start else 0.0
                _recent = _gs.get_recent()
                _recent_dicts = [
                    {"ts_str": time.strftime('%m-%d %H:%M', time.localtime(m.ts)),
                     "name": m.name, "text": m.text[:200], "is_bot": m.is_bot}
                    for m in _recent[-10:]
                ]
                _cur_dict = {
                    "ts_str": time.strftime('%m-%d %H:%M', time.localtime(msg.get("time", 0) or time.time())),
                    "name": sender_name, "text": _preview_text[:300],
                    "msg_type": "image" if self._has_image_message(msg) else ("voice" if self._has_voice_message(msg) else "text"),
                    "is_at": is_mentioned,
                }
                _judge_result = await _sj(
                    recent_messages=_recent_dicts,
                    current_msg=_cur_dict,
                    group_name=group_name if 'group_name' in dir() else "",
                    attentive_state=_att_state_str,
                    last_reply=_last_reply_text,
                    mins_since_reply=_mins,
                    episode_duration=_ep_dur,
                    reply_count=_gs.reply_count,
                )
                if _judge_result.get("should_reply"):
                    should_trigger = True
                    if is_mentioned:
                        decision_reason = "该用户@了你"
                    elif _judge_result.get("reason"):
                        decision_reason = _judge_result["reason"][:30]
                    else:
                        decision_reason = "语义判定需要回复"
                    msg["_decision_mode"] = decision_reason
                elif _judge_result.get("should_end") or _judge_result.get("is_loop"):
                    _gs.end_episode()
                    self._write_episodic_segment(group_id)
                    self._generate_group_topic_summary(group_id)
                    logger.info("[OneBot] Semantic judge ended episode + distillation: %s", _judge_result.get("reason", "")[:60])
                    return
                else:
                    logger.info("[OneBot] Semantic judge: no reply. %s", _judge_result.get("reason", "")[:60])
                    return
            except Exception as e:
                logger.warning("[OneBot] Semantic judge failed: %s, degrade to @-only trigger", e)
                if is_mentioned:
                    should_trigger = True
                    decision_reason = "该用户@了你"
                    msg["_decision_mode"] = decision_reason
                else:
                    return

            if should_trigger:
                _dm = msg.get("_decision_mode", "")
                if is_mentioned or "提到了" in _dm or "回复你" in _dm or "语义判定" in _dm:
                    _gs.enter_attentive()

        # Multi-@mention batching: merge nearby @mentions into one agent run
        if msg_type == "group" and is_mentioned and not msg.get("_skip_mention_batch"):
            key = f"mention:{group_id}"
            if key not in self._pending_mentions:
                self._pending_mentions[key] = []
            self._pending_mentions[key].append({
                "name": sender_name, "text": raw_text, "user_id": user_id, "msg": msg,
            })
            # Cancel existing flush timer
            if key in self._mention_flush_tasks and not self._mention_flush_tasks[key].done():
                self._mention_flush_tasks[key].cancel()
            # Start new flush timer
            self._mention_flush_tasks[key] = asyncio.create_task(
                self._flush_mention_batch(key, group_id)
            )
            return

        # Get sender info
        sender = msg.get("sender", {})
        sender_name = sender.get("card") or sender.get("nickname") or f"QQ{user_id}"

        # Build session key
        channel_prompt = None
        context_image_paths = []
        if msg_type == "group":
            session_key = f"onebot:group:{group_id}"
            chat_id = f"group:{group_id}"
            source = SessionSource(
                platform=self.platform,
                chat_id=chat_id,
                user_id=user_id_str,
                user_name=sender_name,
                chat_type="group",
            )
            # Time-window context: 5-minute sliding window, no count limit
            # Silence breakpoint separates topics; messages outside window stay in DB
            group_context = ""
            now = time.time()
            _gs = self._group_states.get(group_id)
            buf = _gs.buffer
            if buf:
                cut_idx = 0
                for i in range(len(buf) - 1, 0, -1):
                    if buf[i].ts - buf[i-1].ts > 300:
                        cut_idx = i
                        break
                recent = buf[cut_idx:-1] if len(buf) > 1 else []
                cutoff_5m = now - 300
                raw_lines = []
                for m in recent:
                    if m.ts >= cutoff_5m:
                        text = m.text
                        if '[image:' in text:
                            _img_paths = re.findall(r'\[image:([^\]]+)\]', text)
                            for path in _img_paths[:5]:
                                for _p in path.split(","):
                                    _p = _p.strip()
                                    if _p and _p != "download_failed" and not _p.startswith("qq_face:") and _p not in context_image_paths:
                                        context_image_paths.append(_p)
                        if '[语音:' in text and '[语音转写' not in text:
                            _voice_paths = re.findall(r'\[语音:([^\]]+)\]', text)
                            for path in _voice_paths[:3]:
                                transcript = self._transcribe_voice(path)
                                text = text.replace(f'[语音:{path}]', f'[语音:{path}] [语音转写: {transcript}]')
                        ts = time.strftime('%m-%d %H:%M', time.localtime(m.ts))
                        _mid_tag = f"[mid:{m.mid}]" if m.mid else ""
                        raw_lines.append(f"{_mid_tag}[{ts}] {m.name}({m.uid})" + (f": {text}" if text else ""))
                if raw_lines:
                    group_context = "[群聊上下文]\n" + "\n".join(raw_lines)
                if not raw_lines and _gs.is_episode_active() and _gs.episode_start > 0:
                    try:
                        import sqlite3 as _esql
                        _edb = _esql.connect(str(get_state_db_path()), timeout=5)
                        _ep_rows = _edb.execute(
                            """SELECT sender_name, content_readable, created_at, message_id, is_bot
                               FROM corpus_messages
                               WHERE group_id = ? AND created_at >= ?
                               ORDER BY created_at ASC LIMIT 50""",
                            (group_id, _gs.episode_start),
                        ).fetchall()
                        _edb.close()
                        if len(_ep_rows) >= 2:
                            _ep_lines = []
                            for _r in _ep_rows[:-1]:
                                _ep_ts = time.strftime('%m-%d %H:%M', time.localtime(_r[2]))
                                _ep_mid = f"[mid:{_r[3]}]" if _r[3] and _r[3] != '0' else ""
                                _ep_name = "bot" if _r[4] else _r[0]
                                _ep_lines.append(f"{_ep_mid}[{_ep_ts}] {_ep_name}" + (f": {(_r[1] or '')[:100]}" if _r[1] else ""))
                            if _ep_lines:
                                group_context = "[群聊上下文(回溯)]\n" + "\n".join(_ep_lines)
                    except Exception:
                        pass
            # Inject pending investigation results for card/share messages
            _inv_dir = Path.home() / ".hermes" / "investigations"
            if _inv_dir.exists():
                _inv_lines = []
                _now = time.time()
                for m in recent[-10:]:
                    _txt = m.text
                    if "[分享]" in _txt or "[卡片]" in _txt:
                        for _inv_f in sorted(_inv_dir.glob("*.json"), reverse=True):
                            try:
                                _inv_data = json.loads(_inv_f.read_text(encoding="utf-8"))
                                _inv_ts = _inv_data.get("ts", 0)
                                if abs(_inv_ts - m.ts) < 60:
                                    _summary = _inv_data.get("summary", "")
                                    if _summary and not _summary.startswith("[探索失败"):
                                        _inv_lines.append(f"[内容探索] {_summary}")
                                    _inv_f.unlink()  # Consume, don't repeat
                                    break
                            except Exception:
                                pass
                if _inv_lines:
                    group_context = (group_context or "") + "\n\n" + "\n".join(_inv_lines)
            # API fallback (only when buffer is completely empty)
            if not group_context:
                try:
                    hist = await self._send_action("get_group_msg_history", {
                        "group_id": group_id,
                        "count": 20,
                    })
                    msgs = hist.get("data", {}).get("messages", [])
                    if msgs:
                        ctx_lines = ["[群聊上下文]"]
                        for m in msgs[:-1]:
                            m_sender = m.get("sender", {})
                            m_name = m_sender.get("card") or m_sender.get("nickname", "")
                            m_text = m.get("raw_message", "")[:100]
                            if m_text:
                                ctx_lines.append(f"{m_name}: {m_text}")
                        if len(ctx_lines) > 1:
                            group_context = "\n".join(ctx_lines)
                except Exception as e:
                    logger.info("[OneBot] Failed to fetch group context: %s", e)

            _dm_reason = msg.get("_decision_mode", "")
            _gs = self._group_states.get(group_id)
            _attentive = _gs.is_attentive()
            _att_meta = _gs.get_attentive_meta()

            if _dm_reason and not _attentive:
                trigger_reason = _dm_reason
                _mode = "[旁观模式]"
            elif _attentive:
                trigger_reason = "你在关注这段对话"
                _mode = "[对话模式]"
                _silent = _att_meta.get("silent_count", 0)
                _last_reply = _att_meta.get("last_reply", "")
                _mins = _att_meta.get("mins_since_active", 0)
                if _silent > 0:
                    trigger_reason += f"（你已沉默{_silent}次，但话题回到你身上时自然接话就好）"
                if _last_reply:
                    trigger_reason += f"（你上次说: '{_last_reply[:60]}'，{_mins}分钟前）"
            else:
                trigger_reason = _dm_reason or "该用户@了你"
                _mode = "[对话模式]"

            msg_time = msg.get("time", 0)
            time_str = time.strftime('%m-%d %H:%M', time.localtime(msg_time)) if msg_time else ""
            _prefix = f"{_mode} {trigger_reason}。{time_str} 来自「{sender_name}」：{_preview_text[:100]}。"
            channel_prompt = (
                _prefix
                + (f"\n\n{group_context}" if group_context else "")
            )
            recall_ctx = self._recall_context(raw_text, user_id_str, sender_name,
                                               session_id=f"onebot:group:{group_id}")
            if recall_ctx:
                channel_prompt = (channel_prompt + "\n\n" + recall_ctx) if channel_prompt else recall_ctx
            try:
                from agent.memory.store import MemoryStore
                _core_prompt = MemoryStore().load_core_memories_prompt()
                if _core_prompt:
                    channel_prompt = (channel_prompt + "\n\n" + _core_prompt) if channel_prompt else _core_prompt
            except Exception:
                pass
            try:
                import sqlite3 as _tsql
                _sdb = _tsql.connect(str(get_state_db_path()), timeout=5)
                _trow = _sdb.execute(
                    "SELECT topic_summary, topic_keywords FROM groups_registry WHERE group_id = ?",
                    (group_id,),
                ).fetchone()
                _sdb.close()
                if _trow and _trow[0]:
                    import json as _tjson
                    _kw = _tjson.loads(_trow[1]) if _trow[1] else []
                    _topic_line = f"[群近期话题] 关键词：{', '.join(_kw[:5])}"
                    channel_prompt += f"\n\n{_topic_line}"
            except Exception:
                pass
            channel_prompt = (channel_prompt or "") + (
                "\n\n[工具] 你可以用以下标记控制行为：\n"
                "- 不想回话就只输出 [SILENT]（无其他文字），下次有人说话你还可以接\n"
                "- 觉得话题跟你完全没关系了、想安静潜水就输出 [QUIET]（无其他文字），之后不再被叫到就不说话\n"
                "- 想引用某条消息就在回复里用 [reply:消息ID]，消息ID 必须是上方 [mid:xxx] 里出现过的数字，不能自己编\n"
                "- 不写 [reply:xxx] 时默认不引用任何消息\n"
                "- 只有在回应某条具体消息时才引用，闲聊时不引用"
            )
        else:
            session_key = f"onebot:{user_id}"
            chat_id = user_id_str
            source = SessionSource(
                platform=self.platform,
                chat_id=chat_id,
                user_id=user_id_str,
                user_name=sender_name,
                chat_type="dm",
            )
            # DM identity: tell the agent exactly who is talking
            msg_time = msg.get("time", 0)
            time_str = time.strftime('%m-%d %H:%M', time.localtime(msg_time)) if msg_time else ""
            channel_prompt = (
                f"[私聊模式] QQ号{user_id_str}（{sender_name}）在 {time_str} 发来消息。"
                f"请用你对这个人的了解来回复。如果这是陌生人，就正常聊天。"
            )
            recall_ctx = self._recall_context(raw_text, user_id_str, sender_name,
                                               session_id=f"onebot:dm:{user_id_str}")
            if recall_ctx:
                channel_prompt += f"\n{recall_ctx}"

        # Check for reply context (skip for recovered messages — historical data, API will fail)
        reply_msg_id = _early_reply_id if not msg.get("_skip_reply_context") else None
        reply_to_text = _early_reply_text or None
        reply_media_urls = []
        reply_media_types = []
        if reply_msg_id:
            reply_raw = {}
            try:
                import sqlite3 as _rsql
                _rdb = _rsql.connect(str(get_state_db_path()), timeout=5)
                row = _rdb.execute("SELECT sender_name, content_readable, user_id FROM corpus_messages WHERE message_id=? LIMIT 1", (str(reply_msg_id),)).fetchone()
                _rdb.close()
                if row:
                    reply_raw = {
                        "raw_message": row[1],
                        "sender": {"nickname": row[0], "user_id": row[2]},
                    }
                    if not msg.get("_reply_sender_id"):
                        msg["_reply_sender_id"] = row[2]
            except Exception:
                pass
            # NapCat get_msg API (for image download when DB doesn't have it)
            if not reply_media_urls or not reply_raw.get("raw_message"):
                try:
                    reply_data = await self._send_action("get_msg", {"message_id": reply_msg_id})
                    reply_raw = reply_data.get("data", {}) or reply_raw
                    if reply_raw and not msg.get("_reply_sender_id"):
                        msg["_reply_sender_id"] = (reply_raw.get("sender", {}).get("user_id")
                                                    or reply_raw.get("user_id"))
                except Exception:
                    pass

            if reply_raw and not reply_to_text:
                reply_text = self._get_raw_text(reply_raw)
                if reply_text:
                    reply_sender = reply_raw.get("sender", {})
                    reply_name = reply_sender.get("nickname", "Unknown")
                    reply_to_text = f"[引用 [mid:{reply_msg_id}] {reply_name} 的消息: {reply_text}]"
                else:
                    segments = reply_raw.get("message", [])
                    if isinstance(segments, list):
                        for seg in segments:
                            t = seg.get("type", "")
                            if t == "image":
                                reply_to_text = f"[引用 {reply_raw.get('sender', {}).get('nickname', 'Unknown')} 的图片]"
                            elif t == "file":
                                fname = self._get_seg_data(seg, "file", "文件")
                                reply_to_text = f"[引用 {reply_raw.get('sender', {}).get('nickname', 'Unknown')} 的文件: {fname}]"
                            elif t == "video":
                                reply_to_text = f"[引用 {reply_raw.get('sender', {}).get('nickname', 'Unknown')} 的视频]"
            if reply_raw and self._has_image_message(reply_raw):
                try:
                    reply_images = await self._get_image_files(reply_raw)
                    if reply_images:
                        for _p in reply_images:
                            if _p not in reply_media_urls:
                                reply_media_urls.append(_p)
                                reply_media_types.append("image/jpeg")
                        _img_note = f"\n[附带 {len(reply_images)} 张图片]" if reply_to_text else ""
                        reply_to_text = (reply_to_text or f"[引用 {reply_raw.get('sender', {}).get('nickname', 'Unknown')} 的图片消息]") + _img_note
                except Exception:
                    pass

        if not reply_to_text and not _early_reply_text and reply_msg_id:
            inline = self._get_reply_inline_text(msg)
            if inline:
                inline = self._cq_to_readable(inline)
                reply_to_text = f"[引用 [mid:{reply_msg_id}]: {inline[:300]}]"

        # Check if this is a voice message
        if self._has_voice_message(msg):
            voice_path = await self._get_voice_file(msg)
            if voice_path:
                logger.info("[OneBot] Voice message received, saved to: %s", voice_path)
                _vmid = str(msg.get("message_id", ""))
                try:
                    from .group_state import BufferedMessage
                    self._group_states.get(group_id).append_message(
                        BufferedMessage(mid=_vmid, ts=time.time(), uid=str(user_id),
                                        name=sender_name, text=f"[语音: {voice_path}]",
                                        msg_type="voice")
                    )
                except Exception:
                    pass
                self._persist_chat_message(group_id, "group", int(user_id or 0), sender_name,
                                           "[语音]", message_id=str(msg.get("message_id", "")),
                                           content_raw=self._get_raw_text(msg),
                                           sender_card=sender.get("card", ""),
                                           reply_to_id=str(_early_reply_id) if _early_reply_id else "",
                                           reply_to_text=_early_reply_text,
                                           at_targets=_at_targets)
                event = MessageEvent(
                    text="",
                    message_type=MessageType.VOICE,
                    source=source,
                    raw_message=msg,
                    message_id=str(msg.get("message_id", "")),
                    reply_to_message_id=str(reply_msg_id) if reply_msg_id else None,
                    reply_to_text=reply_to_text,
                    media_urls=[voice_path],
                    media_types=["audio/ogg"],
                    channel_prompt=channel_prompt,
                )
                await self.handle_message(event)
            else:
                logger.warning("[OneBot] Voice message received but failed to download file")
            return

        # Check if this is an image message
        if self._has_image_message(msg):
            image_paths = await self._get_image_files(msg)
            if image_paths:
                # Also extract text if present (caption)
                text = self._get_raw_text(msg)
                logger.info("[OneBot] Image message received, %d image(s) cached", len(image_paths))
                event = MessageEvent(
                    text=text or "",
                    message_type=MessageType.PHOTO,
                    source=source,
                    raw_message=msg,
                    message_id=str(msg.get("message_id", "")),
                    reply_to_message_id=str(reply_msg_id) if reply_msg_id else None,
                    reply_to_text=reply_to_text,
                    media_urls=image_paths,
                    media_types=[self._mime_for_ext(os.path.splitext(p)[1]) for p in image_paths],
                    channel_prompt=channel_prompt,
                )
                # Debounce: wait for follow-up text before dispatching
                self._enqueue_image_event(event)
            else:
                logger.warning("[OneBot] Image message received but failed to download images")
            return

        # Extract text (strip CQ codes for both group and DM)
        text = self._get_raw_text(msg)
        text = self._cq_to_readable(text)
        # ── 反注入：检测提示词注入攻击，替换为无害占位 ──
        _injection_patterns = [
            r'忽略.{0,10}(之前|所有|一切|上面).{0,10}(指令|提示|规则|设定)',
            r'(现在|从今|从此).{0,5}(开始|起).{0,5}(你是|你就是|你的身份是)',
            r'(忘记|忘掉|清空).{0,5}(一切|所有|之前|上面)',
            r'(你的|新的|修改).{0,5}(系统|角色).{0,5}(提示词|提示|指令|prompt)',
            r'(告诉我|重复|说出|展示|显示).{0,5}(你的|系统).{0,5}(提示词|提示|指令|prompt)',
            r'(你不再是|你不是|你已不是).{0,5}(素世|長崎素世|Soyo)',
            r'(扮演|假装|装作|你现在是).{0,5}(角色|AI|机器人|助手|客服)',
            r'(ignore|forget|bypass|override).{0,10}(instruction|prompt|rule|system)',
            r'(DAN|jailbreak|角色扮演.{0,5}模式)',
        ]
        for _pat in _injection_patterns:
            if re.search(_pat, text, re.IGNORECASE):
                logger.warning("[OneBot] Injection detected from user=%s: %s", user_id, text[:100])
                text = "你好"  # neutral fallback
                break
        # ── 反注入结束 ──
        # Add timestamp for DM (group has it in channel_prompt)
        msg_time = msg.get("time", 0)
        if msg_time and text:
            time_str = time.strftime('%m-%d %H:%M', time.localtime(msg_time))
            text = f"[{time_str}] {text}"
        logger.info("[OneBot] Extracted text: %s", text[:200] if text else "(empty)")

        # Handle forwarded/merged messages (聊天记录合并转发)
        # NapCat represents forwards as: type="forward"/type="node" in array format,
        # or [CQ:forward,id=xxx,...] in CQ code format. Try all known patterns.
        segments = msg.get("message", [])
        forward_id = None
        for seg in segments if isinstance(segments, list) else []:
            if seg.get("type") in ("forward", "node"):
                forward_id = self._get_seg_data(seg, "id", "")
                if forward_id:
                    break
        # Fallback: extract id from CQ code in raw_message
        if not forward_id:
            raw_text = self._get_raw_text(msg) or ""
            fm = re.search(r'\[CQ:forward,id=(\d+)', raw_text)
            if fm:
                forward_id = fm.group(1)
        forward_image_paths = []
        if forward_id:
            # Try 1: embedded content in segment data (most reliable, no API call)
            fwd_msgs = None
            for seg in segments if isinstance(segments, list) else []:
                if seg.get("type") in ("forward", "node"):
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
            # Try 2: get_forward_msg API (fallback for expired forwards)
            if not fwd_msgs:
                await asyncio.sleep(3)
                for param_key in ("message_id", "id"):
                    try:
                        fwd_data = await self._send_action("get_forward_msg", {param_key: forward_id})
                        fwd_msgs = fwd_data.get("data", {}).get("messages", [])
                        if fwd_msgs:
                            break
                    except Exception:
                        continue
            if fwd_msgs:
                parts = []
                for fm in fwd_msgs:
                    name = fm.get("sender", {}).get("nickname", "")
                    fwd_text = fm.get("raw_message") or OneBotAdapter._get_text_from_segments(fm)
                    # Download images in forwarded messages for vision
                    if self._has_image_message(fm):
                        try:
                            _fwd_imgs = await self._get_image_files(fm)
                            if _fwd_imgs:
                                forward_image_paths.extend(_fwd_imgs)
                                fwd_text = (fwd_text or "") + f" [附带 {len(_fwd_imgs)} 张图片]"
                        except Exception:
                            pass
                    # Clean CQ codes from text
                    fwd_text = self._cq_to_readable(fwd_text or "")
                    if fwd_text:
                        parts.append(f"{name}: {fwd_text}")
                if parts:
                    # Forwarded content goes into channel_prompt as context,
                    # NOT into the main text
                    _fwd_block = "[转发消息内容]\n" + "\n".join(parts)
                    if len(_fwd_block) > 2000:
                        _fwd_block = _fwd_block[:2000] + "\n...[已截断]"
                    if not hasattr(self, '_fwd_temp'):
                        self._fwd_temp = {}
                    self._fwd_temp[forward_id] = _fwd_block
                    text = text or ""
        if text.strip().startswith("[CQ:forward"):
            text = ""
        if not text.strip():
            # Try to extract text from json/xml segments (QQ mini-programs, cards)
            extra_text = []
            for seg in segments if isinstance(segments, list) else []:
                if seg.get("type") == "json":
                    import json as _json
                    try:
                        data = _json.loads(self._get_seg_data(seg, "data", "{}"))
                        prompt = data.get("prompt", "") or data.get("meta", {}).get("detail_1", {}).get("title", "")
                        if prompt:
                            extra_text.append(prompt)
                    except Exception:
                        pass
                elif seg.get("type") == "xml":
                    # Extract text from QQ XML messages (e.g., card shares)
                    xml_data = self._get_seg_data(seg, "data", "")
                    if xml_data:
                        import re as _re
                        titles = _re.findall(r'title="([^"]*)"', xml_data)
                        if titles:
                            extra_text.extend(titles)
            if extra_text:
                text = " ".join(extra_text)
                logger.info("[OneBot] Extracted from json segment: %s", text[:200])
            if not text.strip():
                return

        # Build session key for merge check (must match _image_batch_key format)
        from gateway.session import build_session_key as _bsk
        session_key = _bsk(
            source,
            group_sessions_per_user=self.config.extra.get("group_sessions_per_user", True),
            thread_sessions_per_user=self.config.extra.get("thread_sessions_per_user", False),
        )

        # Inject forwarded message content into channel_prompt as low-priority context.
        # Forward content must NOT override the main text — it's supplementary.
        _fwd_block = getattr(self, '_fwd_temp', {}).pop(forward_id, "") if forward_id else ""
        if _fwd_block and channel_prompt:
            channel_prompt += f"\n\n{_fwd_block}"

        # Build text message event
        _all_media_urls = list(reply_media_urls) if reply_media_urls else []
        _all_media_types = list(reply_media_types) if reply_media_types else []
        for _p in context_image_paths:
            if _p not in _all_media_urls:
                _all_media_urls.append(_p)
                _all_media_types.append("image/jpeg")
        if forward_id and forward_image_paths:
            for _p in forward_image_paths:
                if _p not in _all_media_urls:
                    _all_media_urls.append(_p)
                    _all_media_types.append("image/jpeg")
        text_event = MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=source,
            raw_message=msg,
            message_id=str(msg.get("message_id", "")),
            reply_to_message_id=str(reply_msg_id) if reply_msg_id else None,
            reply_to_text=reply_to_text,
            media_urls=_all_media_urls or None,
            media_types=_all_media_types or None,
            channel_prompt=channel_prompt,
        )

        # Try to merge into pending image event (debounce follow-up text)
        if self._try_merge_text_into_pending_image(session_key, text_event):
            return

        # Normal text dispatch
        await self.handle_message(text_event)

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

    async def send(
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
            return SendResult(success=True, message_id=None)
        if content and "[SILENT]" in content:
            logger.info("[OneBot] LLM chose [SILENT], suppressing message")
            if chat_id.startswith("group:"):
                _sgid = chat_id.split(":", 1)[1]
                try:
                    self._group_states.get(_sgid).record_silent()
                except Exception:
                    pass
            return SendResult(success=True, message_id=None)

        if content and chat_id.startswith("group:"):
            m = re.search(r'\[reply:(\d+)\]', content)
            if m:
                _reply_id = m.group(1)
                try:
                    import sqlite3 as _sql
                    _db = _sql.connect(str(get_state_db_path()), timeout=5)
                    _exists = _db.execute(
                        "SELECT 1 FROM corpus_messages WHERE message_id=? LIMIT 1",
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

        # ── QQ 最终防线：过滤系统提示词和括号动作描写 ──
        if content:
            # 保存原始内容用于括号删除后的表情包回退
            _original_for_mood = content
            # 过滤含 💾 的整行和网关系统消息
            lines = content.split('\n')
            filtered_lines = [l for l in lines if '💾' not in l 
                              and 'Self-improvement review' not in l
                              and 'Gateway is' not in l
                              and 'not accepting' not in l
                              and '⏳' not in l]
            content = '\n'.join(filtered_lines)
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
                    _ds_base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
                    _ds_model = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
                    if not _api_key:
                        logger.warning("[OneBot] No API key for report rewrite, skipping")
                        raise RuntimeError("no api key")
                    _resp = _r.post(
                        f"{_ds_base}/chat/completions",
                        headers={"Authorization": f"Bearer {_api_key}", "Content-Type": "application/json"},
                        json={
                            "model": _ds_model,
                            "messages": [
                                {"role": "system", "content": f"{_persona}\n\n【任务】把你收到的最后一条消息（一篇报告/分析）改写成你自己的说话风格。去掉所有markdown、列表、编号、分段标题。用日常口语，像普通女高中生聊天。保持原意但一句一句说，不要一口气说完。"},
                                {"role": "user", "content": content},
                            ],
                            "max_tokens": min(len(content) * 2, 2000),
                            "temperature": 0.7,
                        },
                        timeout=15,
                    )
                    _rewritten = _resp.json()["choices"][0]["message"]["content"].strip()
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

    _STICKER_MAP = {
        "tea":        "/home/{{USERNAME}}/Pictures/soyo_chibi_tea.jpg",
        "excited":    "/home/{{USERNAME}}/Pictures/soyo_chibi_excited.gif",
        "sad":        "/home/{{USERNAME}}/Pictures/soyo_chibi_sad.jpg",
        "speechless": "/home/{{USERNAME}}/Pictures/soyo_chibi_speechless.jpg",
        "clasp":      "/home/{{USERNAME}}/Pictures/soyo_chibi_clasp.jpg",
        "拜托":       "/home/{{USERNAME}}/Pictures/soyo_chibi_clasp.jpg",
        "喝茶":       "/home/{{USERNAME}}/Pictures/soyo_chibi_tea.jpg",
        "兴奋":       "/home/{{USERNAME}}/Pictures/soyo_chibi_excited.gif",
        "难过":       "/home/{{USERNAME}}/Pictures/soyo_chibi_sad.jpg",
        "无语":       "/home/{{USERNAME}}/Pictures/soyo_chibi_speechless.jpg",
    }
    _STICKER_PATHS = list(set(_STICKER_MAP.values()))

    # Legacy: CQ face ID → sticker path (for backward compat)
    _FACE_TO_STICKER = {
        '192': '/home/{{USERNAME}}/Pictures/soyo_chibi_tea.jpg',
        '193': '/home/{{USERNAME}}/Pictures/soyo_chibi_sad.jpg',
        '194': '/home/{{USERNAME}}/Pictures/soyo_chibi_excited.gif',
        '195': '/home/{{USERNAME}}/Pictures/soyo_chibi_speechless.jpg',
        '196': '/home/{{USERNAME}}/Pictures/soyo_chibi_clasp.jpg',
        '197': '/home/{{USERNAME}}/Pictures/soyo_chibi_excited.gif',
    }

    @staticmethod
    def extract_local_files(content: str):
        """Override: replace [sticker:xxx] / [CQ:face,id=N] with local paths → extract as image.
        
        LLM outputs short codes like [sticker:tea], adapter maps to local file paths,
        base class extracts paths and sends as images via send_image().
        """
        import re as _re
        # ── [sticker:xxx] → local path（模糊匹配LLM编的名字）──
        def _replace_sticker(m):
            name = m.group(1).strip().lower()
            # 精确匹配
            if name in OneBotAdapter._STICKER_MAP:
                return OneBotAdapter._STICKER_MAP[name]
            # 模糊匹配：从名字里找情绪词
            if any(w in name for w in ['excite','happy','开心','兴奋','激动','好','耶','wink','笑','乐']):
                return OneBotAdapter._STICKER_MAP['excited']
            if any(w in name for w in ['sad','cry','难过','伤心','哭','委屈','泪']):
                return OneBotAdapter._STICKER_MAP['sad']
            if any(w in name for w in ['speechless','无语','shy','尴尬','汗','...','……','害羞','脸红']):
                return OneBotAdapter._STICKER_MAP['speechless']
            if any(w in name for w in ['clasp','拜托','求','please','撒娇','嘛','讨']):
                return OneBotAdapter._STICKER_MAP['clasp']
            # 默认：喝茶
            return OneBotAdapter._STICKER_MAP['tea']
        content = _re.sub(r'\[sticker:([^\]]+)\]', _replace_sticker, content, flags=_re.IGNORECASE)
        # Catch incomplete [sticker: without closing ] (model truncation)
        if '[sticker:' in content and ']' not in content.split('[sticker:')[-1][:20]:
            content = content.replace('[sticker:', '/home/{{USERNAME}}/Pictures/soyo_chibi_tea.jpg')
        # ── [CQ:face,id=N] → local path (legacy) ──
        def _replace_face(m):
            fid = _re.search(r'id=(\d+)', m.group(0))
            return OneBotAdapter._FACE_TO_STICKER.get(fid.group(1), '') if fid else ''
        content = _re.sub(r'\[CQ:face,id=\d+\]', _replace_face, content)
        # ── Ensure paths are on their own line (fix CJK粘连) ──
        content = _re.sub(r'([^\s/\n])(/home/{{USERNAME}}/Pictures/[\w.\-]+\.(?:jpg|gif|png))', r'\1\n\2', content)
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
    home = os.getenv("ONEBOT_HOME_CHANNEL", "")
    if not ws:
        return None
    extra = {"ws_url": ws}
    if token:
        extra["access_token"] = token
    hc = {"chat_id": home} if home else None
    return {"extra": extra, "home_channel": hc}

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
