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

        # Group message buffer: store recent messages per group for context
        # Pure 5-minute time window — no count limit. Messages outside the window
        # stay in DB (chat_message_buffer) for permanent storage and can be
        # reloaded via recall/buffer_search when needed.
        self._group_buffer: Dict[str, list] = {}  # group_id → [{name, text, ts}, ...]

        # Image description cache: avoid re-calling vision API for same image
        self._image_descriptions: Dict[str, str] = {}
        self._voice_transcripts: Dict[str, str] = {}
        self._vision_config: Dict[str, str] = {}

        # Smart trigger: LLM decides whether to join conversation
        self._wake_words = {"soyo", "素世", "soyo酱", "そよ", "長崎", "爽世", "清尘", "清塵"}
        self._last_bot_reply: Dict[str, tuple] = {}  # group_id → (timestamp, text)
        self._checked_messages: set = set()

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
        """Add the bot's own reply to the group buffer so the LLM can see it in context.
        Also persists to DB with is_bot=1 so auto-join can detect recent activity."""
        if chat_id.startswith("group:"):
            group_id = chat_id.split(":", 1)[1]
            if group_id not in self._group_buffer:
                self._group_buffer[group_id] = []
            buf = self._group_buffer[group_id]
            label = "[语音]" if is_voice else ""
            buf.append({"name": "bot", "text": f"{label}{text}", "ts": time.time(), "uid": "0"})
            self._persist_chat_message(group_id, "group", 0, "bot", text, is_bot=1)
            self._last_bot_reply[group_id] = (time.time(), text)
        else:
            # DM reply: persist to DB so LLM can see its own messages
            self._persist_chat_message(chat_id, "private", 0, "bot", text, is_bot=1)

    def get_group_buffer_snapshot(self, group_id: str, window_seconds: int = 300) -> list:
        """Export current group buffer for external consumers (e.g., auto-join).
        
        Returns messages within the time window, newest last.
        Each entry: {name, text, ts}
        """
        buf = self._group_buffer.get(group_id, [])
        if not buf:
            return []
        cutoff = time.time() - window_seconds
        return [m for m in buf if m.get("ts", 0) >= cutoff]

    def _persist_chat_message(self, group_id: str, chat_type: str, user_id: int,
                               sender_name: str, content: str, message_id: str = "",
                               created_at: float = None, is_bot: int = 0,
                               *, content_raw: str = "", sender_card: str = "",
                               group_name: str = ""):
        """Queue a chat message for async SQLite persistence (non-blocking).
        
        If created_at is None, uses current time. Set to the original message
        timestamp for recovered/historical messages to avoid time-window pollution.
        is_bot: 1 if this is the bot's own message (for auto-join dedup).
        """
        try:
            self._persist_queue.put_nowait((group_id, chat_type, user_id, sender_name,
                                            content, message_id, created_at, is_bot,
                                            content_raw, sender_card, group_name))
        except asyncio.QueueFull:
            pass

    async def _persist_worker(self):
        """Background worker: drain persist queue → write to SQLite with retry.
        Dual-write: chat_message_buffer (runtime, prunable) + corpus_messages (permanent, training)."""
        import sqlite3, time as _time
        db_path = str(get_state_db_path())
        _corpus_inited = False
        while True:
            try:
                (group_id, chat_type, user_id, sender_name, content, message_id,
                 created_at, is_bot, content_raw, sender_card, group_name) = await self._persist_queue.get()
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
                                    notes TEXT DEFAULT ''
                                );
                            """)
                            _corpus_inited = True
                        db.execute(
                            "INSERT INTO chat_message_buffer (chat_id, chat_type, user_id, sender_name, content, message_id, created_at, is_bot) VALUES (?,?,?,?,?,?,?,?)",
                            (cid, chat_type, str(user_id), sender_name, content, str(message_id), _ts, is_bot),
                        )
                        db.execute(
                            """INSERT INTO corpus_messages
                               (message_id, chat_id, chat_type, group_id, user_id,
                                sender_name, sender_card, content_raw, content_readable,
                                is_bot, created_at)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                            (str(message_id), cid, chat_type,
                             group_id if chat_type == "group" else "",
                             user_id, sender_name, sender_card,
                             content_raw, content, is_bot, _ts),
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

    def _get_sender_identity(self, user_id: str, sender_name: str) -> str:
        """Query LTM for a sender's profile and return a compact identity card.

        Returns empty string if no relevant entries found or query fails.
        """
        try:
            from agent.memory.gateway import UnifiedMemoryGateway
            gw = UnifiedMemoryGateway.get_instance()
            if not gw:
                return ""
            conn = gw._store._get_conn()
            rows = conn.execute(
                "SELECT category, key, value FROM long_term_entries "
                "WHERE key LIKE ? AND confidence >= 0.5 "
                "AND category IN ('user_profile', 'user_preference') "
                "ORDER BY category, confidence DESC LIMIT 8",
                (f"{user_id}%",),
            ).fetchall()
            if not rows:
                return ""

            lines = []
            for cat, key, value in rows:
                text = value.strip()[:80]
                if text:
                    lines.append(text)

            if not lines:
                return ""

            return f"[关于 {sender_name}] " + "；".join(lines)
        except Exception:
            return ""

    def _auto_recall(self, message_text: str, exclude_qq: str = "") -> str:
        """Search LTM for memories relevant to the current message using FTS.

        Returns formatted recall results or empty string if nothing found.
        Excludes entries already shown in the sender's identity card.
        """
        if not message_text or len(message_text.strip()) < 3:
            return ""
        try:
            from agent.memory.gateway import UnifiedMemoryGateway
            gw = UnifiedMemoryGateway.get_instance()
            if not gw:
                return ""
            conn = gw._store._get_conn()

            # Extract meaningful keywords: Chinese bigrams + English words
            import re as _re
            cjk_chars = _re.findall(r'[\u4e00-\u9fff]', message_text)
            bigrams = {"".join(cjk_chars[i:i+2]) for i in range(len(cjk_chars)-1)}
            eng_words = _re.findall(r'[a-zA-Z]{3,}', message_text)
            words = list(bigrams)[:12] + eng_words[:5]
            if not words:
                return ""

            # LIKE query: OR multiple keywords, order by confidence DESC
            clauses = " OR ".join(["value LIKE ?" for _ in words[:8]])
            params = [f"%{w}%" for w in words[:8]]
            rows = conn.execute(
                f"SELECT value, category, key, id, recall_strength FROM long_term_entries "
                f"WHERE ({clauses}) AND confidence >= 0.4 "
                f"AND active = 1 "
                f"AND category NOT IN ('sticker') "
                f"ORDER BY confidence DESC LIMIT 5",
                params,
            ).fetchall()

            if not rows:
                return ""

            # L1 reconsolidate: each recall hit strengthens the memory
            gw._ltm.reconsolidate(rows[0][3])

            # Tag-based expansion: for each hit, pull entries sharing tags
            import json as _json
            seen_keys = {row[2] for row in rows}
            expanded = list(rows)
            for value, cat, key, rid, rstrength in rows[:3]:  # Expand from top 3 hits only
                tag_row = conn.execute(
                    "SELECT tags FROM long_term_entries WHERE key = ?", (key,)
                ).fetchone()
                if not tag_row or not tag_row[0] or tag_row[0] == "[]":
                    continue
                try:
                    entry_tags = _json.loads(tag_row[0])
                except Exception:
                    continue
                for tag in entry_tags[:4]:
                    if len(expanded) >= 8:
                        break
                        sub = conn.execute(
                            "SELECT value, category, key, id, recall_strength FROM long_term_entries "
                            "WHERE tags LIKE ? AND key != ? "
                            "AND active = 1 "
                            "AND confidence >= 0.4 AND key NOT IN ({}) "
                            "LIMIT 2".format(",".join("?" * len(seen_keys))),
                            [f"%{tag}%", key] + list(seen_keys),
                        ).fetchall()
                        for v, c, k, rid, rstrength in sub:
                            if k not in seen_keys:
                                expanded.append((v, c, k, rid, rstrength))
                                seen_keys.add(k)

            lines = []
            for row in expanded[:5]:  # Limit total to 5
                value, cat, key, rid, rstrength = row
                # Skip entries from the sender (already in identity card)
                if exclude_qq and key.startswith(f"{exclude_qq}_"):
                    continue
                text = value.strip()[:100]
                if text:
                    # L5: mark uncertain memories
                    if rstrength is not None and rstrength < 0.3:
                        text = f"[不确定] {text}"
                    lines.append(text)

            if not lines:
                return ""

            result = "[相关记忆] " + "；".join(lines)

            try:
                matched_wfs = gw._wfm.match_trigger(message_text)
                if matched_wfs:
                    wf_hints = []
                    for wf in matched_wfs[:2]:
                        if wf.current_weight > 0.15:
                            steps_preview = " → ".join(wf.steps[:3]) if wf.steps else ""
                            wf_hints.append(f"{wf.name}({wf.current_weight:.2f}): {steps_preview}")
                    if wf_hints:
                        result += "\n[可用行为模式] " + " | ".join(wf_hints)
            except Exception:
                pass

            return result
        except Exception:
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
            # Debug: log reply_message_id if present
            rid = params.get("reply_message_id", "NOT SET")
            print(f"[ONEBOT-SEND] action={action} reply_message_id={rid} msg_len={len(str(params.get('message','')))}", flush=True)
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

    @staticmethod
    def _cq_to_readable(text: str) -> str:
        """Convert CQ codes in raw_message to human-readable text.
        
        - [CQ:at,qq=xxx] → @QQxxx
        - [CQ:at,qq=all] → @全体成员
        - [CQ:reply,id=xxx] → [回复消息]
        - [CQ:image,...] → [图片]
        - [CQ:face,...] → [动画表情]
        - [CQ:video,...] → [视频]
        - [CQ:file,...] → [文件]
        - [CQ:json,...] → [分享: 标题] / [分享]
        - [CQ:xml,...]  → [卡片: 标题] / [卡片]
        - All others → removed
        """
        if not text:
            return ""
        text = re.sub(r'\[CQ:at,qq=(\d+)\]', r'@QQ\1', text)
        text = re.sub(r'\[CQ:at,qq=all\]', '@全体成员', text)
        text = re.sub(r'\[CQ:reply,id=\d+\]', '[回复消息]', text)
        text = re.sub(r'\[CQ:image,[^\]]*\]', '[图片]', text)
        text = re.sub(r'\[CQ:face,[^\]]*\]', '[动画表情]', text)
        text = re.sub(r'\[CQ:video,[^\]]*\]', '[视频]', text)
        text = re.sub(r'\[CQ:file,[^\]]*\]', '[文件]', text)
        # Card/share messages: extract title if available, preserve as marker
        text = re.sub(r'\[CQ:json,[^\]]*\]', lambda m: f'[分享]', text)
        text = re.sub(r'\[CQ:xml,[^\]]*\]', lambda m: f'[卡片]', text)
        text = re.sub(r'\[CQ:[^\]]+\]', '', text)
        return text.strip()

    def _is_reply_to_bot(self, msg: dict) -> bool:
        """Check if this message is a reply to one of the bot's own messages."""
        sid = msg.get("_reply_sender_id")
        return sid and str(sid) == str(self._self_id)

    def _should_check(self, msg: dict, group_id: str, user_id, raw_text: str) -> bool:
        """Determine whether the LLM should decide if it wants to join the conversation.

        Returns True for: reply to bot, wake word, or recent bot activity in the group.
        """
        # Dedup: don't check the same message twice
        msg_id = str(msg.get("message_id", ""))
        if msg_id in self._checked_messages:
            return False
        self._checked_messages.add(msg_id)
        if len(self._checked_messages) > 500:
            self._checked_messages.clear()

        # 1. Someone replied to the bot
        if self._is_reply_to_bot(msg):
            return True

        # 2. Message contains a wake word
        text_lower = raw_text.lower() if raw_text else ""
        if any(w in text_lower for w in self._wake_words):
            return True

        # 3. Bot recently spoke and the topic might be ongoing — but skip
        #    pure cards/shares (they have no user-generated text content)
        _clean = self._cq_to_readable(raw_text) if raw_text else ""
        _is_card = not _clean.strip() or _clean.strip() in ("[分享]", "[卡片]", "[文件]", "[聊天记录]", "")
        if _is_card:
            return False
        last = self._last_bot_reply.get(group_id)
        if last and time.time() - last[0] < 300:
            return True

        # Episode boundary: bot hasn't spoken in >5min → archive previous segment
        if last and time.time() - last[0] >= 300:
            self._write_episodic_segment(group_id)

        return False

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
                """SELECT sender_name, content, is_bot, created_at, user_id
                   FROM chat_message_buffer
                   WHERE chat_id = ? AND created_at > ? AND created_at <= ?
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

    def _get_trigger_reason(self, msg: dict, raw_text: str) -> str:
        """Explain why the bot is being asked to decide whether to reply."""
        if self._is_reply_to_bot(msg):
            return "有人在回复你"
        text_lower = raw_text.lower() if raw_text else ""
        for w in self._wake_words:
            if w in text_lower:
                return f"有人提到了'{w}'"
        return "你最近在这个群说过话，话题可能在延续"

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
        """Check if the message contains an image, face emoji, or sticker segment."""
        segments = msg.get("message", [])
        if not isinstance(segments, list):
            return False
        return any(seg.get("type") in ("image", "face", "mface") for seg in segments)

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
            # Load vision config once
            if not self._vision_config:
                import yaml as _y
                cfg_path = Path.home() / ".hermes" / "config.yaml"
                if cfg_path.exists():
                    cfg = _y.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
                    vis = cfg.get("auxiliary", {}).get("vision", {})
                    self._vision_config = {
                        "api_key": vis.get("api_key", ""),
                        "base_url": vis.get("base_url", ""),
                        "model": vis.get("model", ""),
                    }
            if not self._vision_config.get("api_key"):
                self._image_descriptions[image_path] = "图片"
                return "图片"

            import base64
            with open(image_path, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode()
            ext = os.path.splitext(image_path)[1].lower()
            mime = self._mime_for_ext(ext)

            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
                resp = await client.post(
                    f"{self._vision_config['base_url']}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._vision_config['api_key']}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._vision_config["model"],
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
                data = resp.json()
                desc = data["choices"][0]["message"]["content"].strip()
        except Exception as e:
            logger.warning("[OneBot] Image description failed for %s: %s", image_path, e)
            desc = "图片"

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
        groups = list(self._group_buffer.keys())
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
                    buf = self._group_buffer.setdefault(group_id, [])
                    buf.append({"name": sname, "text": buf_text, "ts": ts, "uid": str(sid)})
                    self._persist_chat_message(group_id, "group", int(sid or 0), sname, text,
                                               message_id=str(m.get("real_id", m.get("message_id", ""))),
                                               created_at=ts,
                                               content_raw=text,
                                               sender_card=sender.get("card", ""))
                    # Only respond to real @mentions within 3 minutes of last activity
                    if m.get("_is_placeholder"):
                        continue
                    if self._is_mentioned(m, self._self_id or 0):
                        # Check if conversation is still "alive" — look at last non-bot message
                        last_other_ts = 0
                        for bm in reversed(buf[:-1]):  # skip current (just appended)
                            if bm.get("name") != "bot":
                                last_other_ts = bm.get("ts", 0)
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
        group_id = msg.get("group_id")
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
        group_id = msg.get("group_id")
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
        if msg_type == "group" and effective_self_id:
            is_mentioned = self._is_mentioned(msg, effective_self_id)
            raw_text = self._get_raw_text(msg).strip()
            # Strip reply prefixes that NapCat prepends
            raw_text = re.sub(r'^\[回复[^\]]*\]\s*', '', raw_text)
            raw_text = re.sub(r'^\[Re[^\]]*\]\s*', '', raw_text)
            raw_text = raw_text.strip()

            # Pre-download images in group messages for context (even if lurking)
            _image_hint = ""
            if self._has_image_message(msg):
                try:
                    _img_paths = await self._get_image_files(msg)
                    if _img_paths:
                        _image_hint = " [image:" + ",".join(_img_paths) + "]"
                    else:
                        _image_hint = " [image:download_failed]"
                except Exception:
                    _image_hint = " [image:download_failed]"

            # Buffer ALL group messages for context
            # Extract forwarded/merged chat records from segment data BEFORE cleaning
            _fwd_text = ""
            _segments = msg.get("message", [])
            _seg_types = [s.get("type","?") for s in _segments] if isinstance(_segments, list) else []
            print(f"[ONEBOT-DEBUG] msg_id={msg.get('message_id')} raw_len={len(raw_text or '')} seg_count={len(_segments)} types={_seg_types}", flush=True)
            # For forwards, the segment data may be CQ-encoded stub — always fetch via API
            if "forward" in _seg_types or "node" in _seg_types or "CQ:forward" in (raw_text or ""):
                try:
                    _full = await self._send_action("get_forward_msg", {"message_id": msg.get("message_id")})
                    _segments = (_full.get("data", {}) or {}).get("messages", [])  # API returns "messages", not "message"
                    print(f"[ONEBOT-DEBUG] get_forward_msg returned {len(_segments)} messages", flush=True)
                except Exception as e:
                    print(f"[ONEBOT-DEBUG] get_forward_msg failed: {e}", flush=True)
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
                print(f"[ONEBOT-DEBUG] Extracted forward: {len(_fwd_parts)} msgs, {len(_fwd_image_paths)} images", flush=True)
            # Clean CQ codes — use forward text if available, otherwise raw text
            _clean_text = _fwd_text if _fwd_text else self._cq_to_readable(raw_text)
            # Inject downloaded forward images into buffer so vision tool can process them
            if _fwd_image_paths:
                _clean_text += " [image:" + ",".join(_fwd_image_paths) + "]"
            if group_id not in self._group_buffer:
                self._group_buffer[group_id] = []
            buf = self._group_buffer[group_id]
            m_text = (_clean_text + _image_hint)  # full text — no truncation, model can handle it
            buf.append({"name": sender_name, "text": m_text, "ts": time.time(), "uid": str(user_id)})
            # Persist to SQLite buffer — permanent archive, time-based window for context
            _msg_id = str(msg.get("message_id", ""))
            self._persist_chat_message(group_id, "group", int(user_id), sender_name, m_text, _msg_id,
                                       content_raw=raw_text,
                                       sender_card=sender.get("card", ""))

            # Spawn background investigation for card/share messages
            if "[分享]" in _clean_text or "[卡片]" in _clean_text:
                _script = str(Path.home() / ".hermes" / "subagents" / "investigate")
                _raw = msg.get("raw_message", "")
                subprocess.Popen(
                    [_sys.executable, _script, _raw, _msg_id],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )

            # Trigger: @mention → mandatory reply. Also allow smart-trigger for replies/wake-words.
            should_trigger = is_mentioned
            if not should_trigger:
                decision_reason = self._get_trigger_reason(msg, raw_text)
                if self._should_check(msg, group_id, user_id, raw_text):
                    should_trigger = True
                    msg["_decision_mode"] = decision_reason
                else:
                    logger.info("[OneBot] Group message without trigger, lurking")
                    return

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
            # Priority 1: in-memory buffer with time filtering
            buf = self._group_buffer.get(group_id, [])
            if buf:
                # Detect silence breakpoint (>5 min gap = new topic)
                cut_idx = 0
                for i in range(len(buf) - 1, 0, -1):
                    if buf[i]['ts'] - buf[i-1]['ts'] > 300:
                        cut_idx = i
                        break
                recent = buf[cut_idx:-1] if len(buf) > 1 else []  # exclude current msg
                # Within 5 min = full context, beyond = drop (stored in DB, only fetched on reply)
                cutoff_5m = now - 300
                raw_lines = []
                for m in recent:
                    if m['ts'] >= cutoff_5m:
                        text = m.get('text', '')
                        # Enrich image references with vision model descriptions
                        if '[image:' in text:
                            _img_paths = re.findall(r'\[image:([^\]]+)\]', text)
                            for path in _img_paths[:5]:
                                desc = await self._describe_image(path)
                                text = text.replace(f'[image:{path}]', f'[图片: {desc}]')
                        # Transcribe voice messages with local faster-whisper
                        if '[语音:' in text:
                            _voice_paths = re.findall(r'\[语音:([^\]]+)\]', text)
                            for path in _voice_paths[:3]:
                                transcript = self._transcribe_voice(path)
                                text = text.replace(f'[语音:{path}]', f'[语音: {transcript}]')
                        ts = time.strftime('%m-%d %H:%M', time.localtime(m['ts']))
                        raw_lines.append(f"[{ts}] {m['name']}({m.get('uid','')})" + (f": {text}" if text else ""))
                if raw_lines:
                    group_context = "[群聊上下文]\n" + "\n".join(raw_lines)
            # Inject pending investigation results for card/share messages
            _inv_dir = Path.home() / ".hermes" / "investigations"
            if _inv_dir.exists():
                _inv_lines = []
                _now = time.time()
                for m in recent[-10:]:  # Check last 10 messages
                    _txt = m.get("text", "")
                    if "[分享]" in _txt or "[卡片]" in _txt:
                        # Look for investigation results — match by timestamp proximity
                        for _inv_f in sorted(_inv_dir.glob("*.json"), reverse=True):
                            try:
                                _inv_data = json.loads(_inv_f.read_text(encoding="utf-8"))
                                _inv_ts = _inv_data.get("ts", 0)
                                if abs(_inv_ts - m.get("ts", 0)) < 60:  # Within 60s of msg
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

            # Extract text for channel_prompt (need it before MessageEvent creation)
            _preview_text = ""
            if self._has_voice_message(msg):
                _preview_text = "[语音消息]"
            elif self._has_image_message(msg):
                _preview_text = "[图片消息]"
            else:
                _preview_text = self._cq_to_readable(self._get_raw_text(msg) or "")
            # Inject group chat context: identify WHO sent the message and WHAT they said
            _dm_reason = msg.get("_decision_mode", "")
            if _dm_reason:
                trigger_reason = _dm_reason
            else:
                trigger_reason = "该用户@了你"
            msg_time = msg.get("time", 0)
            time_str = time.strftime('%m-%d %H:%M', time.localtime(msg_time)) if msg_time else ""
            _prefix = (f"[旁观模式] {trigger_reason}。" if _dm_reason
                       else f"[群聊模式] {time_str} 来自「{sender_name}」：{_preview_text[:100]}。")
            channel_prompt = (
                _prefix + trigger_reason
                + (f"\n\n{group_context}" if group_context else "")
            )
            # Extract buffered image paths from group context so the AI can
            # actually see them via vision enrichment (instead of raw path strings).
            if "[image:" in group_context:
                import re as _rc
                for _m in _rc.finditer(r'\[image:([^\]]+)\]', group_context):
                    for _p in _m.group(1).split(","):
                        _p = _p.strip()
                        if _p and _p != "download_failed" and not _p.startswith("qq_face:") and _p not in context_image_paths:
                            context_image_paths.append(_p)
            # Inject sender identity card for group messages
            identity = self._get_sender_identity(user_id_str, sender_name)
            if identity:
                channel_prompt = (channel_prompt + "\n\n" + identity) if channel_prompt else identity
            recall = self._auto_recall(raw_text, exclude_qq=user_id_str)
            if recall:
                channel_prompt = (channel_prompt + "\n\n" + recall) if channel_prompt else recall
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
            identity = self._get_sender_identity(user_id_str, sender_name)
            if identity:
                channel_prompt += f"\n{identity}"
            recall = self._auto_recall(raw_text, exclude_qq=user_id_str)
            if recall:
                channel_prompt += f"\n{recall}"

        # Check for reply context (skip for recovered messages — historical data, API will fail)
        reply_msg_id = self._get_reply_message_id(msg) if not msg.get("_skip_reply_context") else None
        reply_to_text = None
        reply_media_urls = []
        if not reply_msg_id and msg.get("_skip_reply_context"):
            # Recovered message: try to find reply context from local buffer
            # instead of calling get_msg API (which fails for old messages)
            _recover_rid = self._get_reply_message_id(msg)
            if _recover_rid:
                try:
                    import sqlite3
                    db = sqlite3.connect(str(get_state_db_path()))
                    row = db.execute("SELECT sender_name, content FROM chat_message_buffer WHERE message_id=? LIMIT 1", (str(_recover_rid),)).fetchone()
                    db.close()
                    if row:
                        reply_to_text = f"[引用 {row[0]} 的消息: {row[1][:200]}]"
                except Exception:
                    pass
        reply_media_types = []
        if reply_msg_id:
            # Try 1: local SQLite buffer (always has text, faster, no NapCat dependency)
            reply_raw = {}
            try:
                import sqlite3
                db = sqlite3.connect(str(get_state_db_path()))
                row = db.execute("SELECT sender_name, content, user_id FROM chat_message_buffer WHERE message_id=? LIMIT 1", (str(reply_msg_id),)).fetchone()
                db.close()
                if row:
                    reply_raw = {
                        "raw_message": row[1],
                        "sender": {"nickname": row[0], "user_id": row[2]},
                    }
                    if not msg.get("_reply_sender_id"):
                        msg["_reply_sender_id"] = row[2]
            except Exception:
                pass
            # Try 2: NapCat get_msg API (for image download when DB doesn't have it)
            if not reply_media_urls or not reply_raw.get("raw_message"):
                try:
                    reply_data = await self._send_action("get_msg", {"message_id": reply_msg_id})
                    reply_raw = reply_data.get("data", {}) or reply_raw
                    # Store reply sender ID so standalone checks can see who was replied to
                    if reply_raw and not msg.get("_reply_sender_id"):
                        msg["_reply_sender_id"] = (reply_raw.get("sender", {}).get("user_id")
                                                    or reply_raw.get("user_id"))
                except Exception:
                    pass

            if reply_raw:
                reply_text = self._get_raw_text(reply_raw)
                if reply_text:
                    reply_sender = reply_raw.get("sender", {})
                    reply_name = reply_sender.get("nickname", "Unknown")
                    reply_to_text = f"[引用 {reply_name} 的消息: {reply_text}]"
                else:
                    # Fallback for non-text replies (stickers, files, etc.)
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
                # Download replied images so the bot can see them
                if self._has_image_message(reply_raw):
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

        # Fallback: inline reply text from the segment itself
        # Many OneBot implementations embed the quoted text directly in data.text/message
        if not reply_to_text:
            inline = self._get_reply_inline_text(msg)
            if inline:
                inline = self._cq_to_readable(inline)
                reply_to_text = f"[引用: {inline[:300]}]"

        # Check if this is a voice message
        if self._has_voice_message(msg):
            voice_path = await self._get_voice_file(msg)
            if voice_path:
                logger.info("[OneBot] Voice message received, saved to: %s", voice_path)
                # Buffer with path so context builder can transcribe it
                if group_id not in self._group_buffer:
                    self._group_buffer[group_id] = []
                self._group_buffer[group_id].append({
                    "name": sender_name, "text": f"[语音: {voice_path}]", "ts": time.time(), "uid": str(user_id)
                })
                self._persist_chat_message(group_id, "group", int(user_id or 0), sender_name,
                                           "[语音]", message_id=str(msg.get("message_id", "")),
                                           content_raw=self._get_raw_text(msg),
                                           sender_card=sender.get("card", ""))
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
        # NOTE: context_image_paths intentionally NOT added to media_urls —
        # they belong to OTHER senders and are already attributed in channel_prompt.
        # Adding them here would make vision analysis confuse who sent which image.
        # Include images from forwarded messages for vision analysis
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
            group_id = msg.get("group_id")
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
        self._group_buffer.setdefault(group_id, []).append({
            "name": f"[文件] {user_name}",
            "text": f"[群文件上传: {user_name} 上传了 {file_name} ({size_kb}KB). 用 qq-group-file list {group_id} 查看，序号下载]",
            "ts": time.time(),
            "uid": str(msg.get("user_id", "")),
        })
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
        """Send a text message to a QQ chat. Multi-paragraph content is split into separate messages (simulates human typing)."""
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
                    _api_key = "{{DEEPSEEK_API_KEY}}"
                    _resp = _r.post(
                        "https://api.deepseek.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {_api_key}", "Content-Type": "application/json"},
                        json={
                            "model": "deepseek-v4-flash",
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
                gid = int(chat_id.split(":", 1)[1])
                result = await self._send_action("send_group_msg", {"group_id": gid, "message": message})
            else:
                uid = int(chat_id)
                result = await self._send_action("send_private_msg", {"user_id": uid, "message": message})
            if result and result.get("data"):
                return SendResult(success=True, message_id=str(result["data"].get("message_id", "")))
            return SendResult(success=True)
        except Exception as e:
            return SendResult(success=False, error=str(e))
   