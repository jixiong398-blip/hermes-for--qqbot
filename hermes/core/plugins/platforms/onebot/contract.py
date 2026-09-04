"""Pure OneBot ingress/delivery contracts.

The adapter still owns WebSocket/HTTP I/O and QQ-specific policy.  This module
only normalizes a bounded OneBot v11 message into product-level objects so
Gateway code never needs to inspect CQ segments or NapCat field names.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple
from urllib.parse import unquote


MAX_ENVELOPE_TEXT_CHARS = 50_000
MAX_SENDER_NAME_CHARS = 200
MAX_MEDIA_REF_CHARS = 2_048


@dataclass(frozen=True)
class MediaRef:
    """A platform-neutral reference to an attachment; no bytes are loaded."""

    kind: str
    ref: str
    mime_type: Optional[str] = None


@dataclass(frozen=True)
class MessageEnvelope:
    """Validated OneBot message without the original platform payload."""

    message_id: Optional[str]
    platform: str
    chat_id: str
    thread_id: Optional[str]
    sender_id: Optional[str]
    sender_name: Optional[str]
    chat_type: str
    text: str
    media: Tuple[MediaRef, ...] = ()
    reply_to: Optional[str] = None
    timestamp: float = 0.0
    flags: Mapping[str, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionRef:
    """Stable routing key and persistence id for one normalized conversation."""

    session_key: str
    session_id: str
    platform: str
    chat_id: str
    thread_id: Optional[str]
    user_scope: Optional[str] = None
    parent_session_id: Optional[str] = None
    boundary_reason: Optional[str] = None


@dataclass(frozen=True)
class DeliveryIntent:
    """Idempotent outbound intent, independent of OneBot action names."""

    session_id: str
    platform: str
    chat_id: str
    thread_id: Optional[str]
    text: str
    media: Tuple[MediaRef, ...] = ()
    reply_to: Optional[str] = None
    idempotency_key: str = ""


@dataclass(frozen=True)
class OneBotCapabilitySnapshot:
    """Capabilities the Gateway may rely on without probing NapCat."""

    platform: str = "onebot"
    protocol: str = "onebot.v11"
    supports_message_editing: bool = False
    supports_system_messages: bool = False
    supports_group_mentions: bool = True
    supports_replies: bool = True
    supports_images: bool = True
    supports_voice: bool = True
    supports_files: bool = True
    connected: bool = False
    ws_loopback: bool = True
    http_loopback: bool = True
    auth_configured: bool = False
    transport_error_code: Optional[str] = None


def _bounded_text(value: Any, limit: int) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _segment_data(segment: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = segment.get("data")
    return raw if isinstance(raw, Mapping) else {}


def _timestamp(payload: Mapping[str, Any]) -> float:
    raw = payload.get("time")
    try:
        value = float(raw)
        if math.isfinite(value) and value > 0:
            return value
    except (TypeError, ValueError):
        pass
    return time.time()


def _normalize_raw_message_text(value: Any) -> str:
    """Extract text from a string-form OneBot message without raw CQ data."""
    raw = _bounded_text(value, MAX_ENVELOPE_TEXT_CHARS)
    if not raw:
        return ""

    def replace_segment(match: re.Match[str]) -> str:
        segment_type = (match.group(1) or "").strip().lower()
        params = match.group(2) or ""
        if segment_type == "text":
            for item in params.split(","):
                key, separator, item_value = item.partition("=")
                if key.strip().lower() == "text" and separator:
                    return unquote(item_value)[:2_000]
            return ""
        if segment_type in {"image", "mface"}:
            return "[image]"
        if segment_type in {"record", "voice", "audio"}:
            return "[voice]"
        if segment_type == "video":
            return "[video]"
        if segment_type in {"file", "document"}:
            return "[file]"
        if segment_type == "at":
            for item in params.split(","):
                key, separator, item_value = item.partition("=")
                if key.strip().lower() == "qq" and separator:
                    return f"@{_bounded_text(item_value, 80)}"
            return "[@]"
        if segment_type == "reply":
            return "[reply]"
        return f"[{segment_type}]" if segment_type else ""

    normalized = re.sub(r"\[CQ:([A-Za-z0-9_-]+)(?:,([^\]]*))?\]", replace_segment, raw)
    # A malformed/unrecognized raw fragment is still user text, but it must
    # not carry a whole CQ parameter blob into the model context.
    return normalized[:MAX_ENVELOPE_TEXT_CHARS].strip()


def _text_from_segments(
    payload: Mapping[str, Any],
    *,
    bot_id: Optional[str],
) -> tuple[str, Tuple[MediaRef, ...], Dict[str, bool], Optional[str]]:
    segments = payload.get("message")
    if not isinstance(segments, list):
        raw = payload.get("message")
        if not isinstance(raw, str) or not raw.strip():
            raw = payload.get("raw_message")
        raw = _normalize_raw_message_text(raw)
        return raw, (), {}, None

    text_parts: List[str] = []
    media: List[MediaRef] = []
    flags: Dict[str, bool] = {}
    reply_to: Optional[str] = None
    for segment in segments:
        if not isinstance(segment, Mapping):
            continue
        kind = str(segment.get("type") or "").strip().lower()
        data = _segment_data(segment)
        if kind == "text":
            value = data.get("text")
            if value is not None:
                text_parts.append(str(value))
        elif kind == "at":
            target = _bounded_text(data.get("qq"), 80)
            if target:
                if target in {"all", "全体"}:
                    flags["at_all"] = True
                    text_parts.append("@全体")
                else:
                    text_parts.append(f"@{target}")
                    if bot_id and target == bot_id:
                        flags["mentioned"] = True
        elif kind == "reply":
            candidate = _bounded_text(data.get("id"), 120)
            if candidate:
                reply_to = candidate
                flags["reply"] = True
        elif kind in {"image", "mface"}:
            ref = _bounded_text(data.get("url") or data.get("file"), MAX_MEDIA_REF_CHARS)
            if ref:
                media.append(MediaRef("image", ref, "image/*"))
            text_parts.append("[image]")
        elif kind in {"record", "audio", "voice"}:
            ref = _bounded_text(data.get("url") or data.get("file"), MAX_MEDIA_REF_CHARS)
            if ref:
                media.append(MediaRef("voice", ref, "audio/*"))
            text_parts.append("[voice]")
        elif kind == "video":
            ref = _bounded_text(data.get("url") or data.get("file"), MAX_MEDIA_REF_CHARS)
            if ref:
                media.append(MediaRef("video", ref, "video/*"))
            text_parts.append("[video]")
        elif kind in {"file", "document"}:
            ref = _bounded_text(data.get("url") or data.get("file") or data.get("id"), MAX_MEDIA_REF_CHARS)
            if ref:
                media.append(MediaRef("file", ref, None))
            text_parts.append("[file]")
        elif kind == "face":
            face_id = _bounded_text(data.get("id"), 40)
            text_parts.append(f"[face:{face_id}]" if face_id else "[face]")
        elif kind:
            # Unknown segments are represented by a bounded marker, never by
            # their raw data dictionary (which may contain secrets or blobs).
            text_parts.append(f"[{kind}]")
    return " ".join(part for part in text_parts if part).strip()[:MAX_ENVELOPE_TEXT_CHARS], tuple(media), flags, reply_to


def normalize_onebot_message(
    payload: Any,
    *,
    bot_id: Optional[str] = None,
) -> Optional[MessageEnvelope]:
    """Normalize a OneBot v11 message event or return ``None`` if malformed."""
    if not isinstance(payload, Mapping):
        return None
    if str(payload.get("post_type") or "message") != "message":
        return None
    message_type = str(payload.get("message_type") or "").strip().lower()
    if message_type not in {"group", "private"}:
        return None

    sender = payload.get("sender")
    sender = sender if isinstance(sender, Mapping) else {}
    sender_id = _bounded_text(payload.get("user_id") or sender.get("user_id"), 120)
    if not sender_id:
        return None
    chat_id = _bounded_text(
        payload.get("group_id") if message_type == "group" else sender_id,
        160,
    )
    if not chat_id:
        return None

    effective_bot_id = _bounded_text(bot_id, 120) or None
    text, media, flags, segment_reply = _text_from_segments(
        payload,
        bot_id=effective_bot_id,
    )
    reply_to = segment_reply or _bounded_text(payload.get("reply_to"), 120) or None
    if reply_to:
        flags["reply"] = True
    if payload.get("_poke_wake"):
        flags["poke_wake"] = True
    if payload.get("_internal"):
        flags["internal"] = True

    thread_id = _bounded_text(
        payload.get("thread_id") or payload.get("message_thread_id"),
        160,
    ) or None
    message_id = _bounded_text(payload.get("message_id"), 160) or None
    sender_name = _bounded_text(
        sender.get("card") or sender.get("nickname"),
        MAX_SENDER_NAME_CHARS,
    ) or None
    return MessageEnvelope(
        message_id=message_id,
        platform="onebot",
        chat_id=chat_id,
        thread_id=thread_id,
        sender_id=sender_id,
        sender_name=sender_name,
        chat_type="group" if message_type == "group" else "dm",
        text=text,
        media=media,
        reply_to=reply_to,
        timestamp=_timestamp(payload),
        flags=dict(flags),
    )


def session_ref_for_envelope(
    envelope: MessageEnvelope,
    *,
    session_id: Optional[str] = None,
    user_scope: Optional[str] = None,
    parent_session_id: Optional[str] = None,
    boundary_reason: Optional[str] = None,
) -> SessionRef:
    """Derive routing/persistence ids without reading platform payloads."""
    scope = _bounded_text(user_scope, 160) or None
    key = f"onebot:{envelope.chat_type}:{envelope.chat_id}"
    if envelope.thread_id:
        key += f":thread:{envelope.thread_id}"
    if scope:
        key += f":user:{scope}"
    durable_id = _bounded_text(session_id, 240) or key
    return SessionRef(
        session_key=key,
        session_id=durable_id,
        platform=envelope.platform,
        chat_id=envelope.chat_id,
        thread_id=envelope.thread_id,
        user_scope=scope,
        parent_session_id=_bounded_text(parent_session_id, 240) or None,
        boundary_reason=_bounded_text(boundary_reason, 40) or None,
    )


def delivery_intent_for_envelope(
    envelope: MessageEnvelope,
    *,
    session_id: str,
    text: str,
    media: Optional[List[MediaRef]] = None,
    reply_to: Optional[str] = None,
) -> DeliveryIntent:
    """Build a deterministic delivery intent suitable for retry deduplication."""
    durable_session = _bounded_text(session_id, 240)
    bounded_text = _bounded_text(text, MAX_ENVELOPE_TEXT_CHARS)
    bounded_reply = _bounded_text(reply_to or envelope.reply_to, 120) or None
    digest_source = "|".join(
        (
            durable_session,
            _bounded_text(envelope.message_id, 160),
            envelope.chat_id,
            bounded_reply or "",
            bounded_text,
        )
    )
    key = hashlib.sha256(digest_source.encode("utf-8", errors="replace")).hexdigest()[:32]
    return DeliveryIntent(
        session_id=durable_session,
        platform=envelope.platform,
        chat_id=envelope.chat_id,
        thread_id=envelope.thread_id,
        text=bounded_text,
        media=tuple(media or envelope.media),
        reply_to=bounded_reply,
        idempotency_key=key,
    )
