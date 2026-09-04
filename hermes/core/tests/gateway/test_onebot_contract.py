"""Offline OneBot ingress/delivery contract tests.

No WebSocket, HTTP client, NapCat process, or real account data is used here.
"""

from plugins.platforms.onebot.contract import (
    MAX_ENVELOPE_TEXT_CHARS,
    DeliveryIntent,
    MessageEnvelope,
    OneBotCapabilitySnapshot,
    delivery_intent_for_envelope,
    normalize_onebot_message,
    session_ref_for_envelope,
)


def test_group_message_normalizes_mentions_media_reply_without_raw_payload():
    payload = {
        "post_type": "message",
        "message_type": "group",
        "group_id": 24680,
        "user_id": 13579,
        "message_id": 42,
        "time": 1_700_000_000,
        "sender": {"card": "Member", "nickname": "Nickname"},
        "message": [
            {"type": "at", "data": {"qq": "999"}},
            {"type": "text", "data": {"text": "请看这个"}},
            {"type": "image", "data": {"url": "https://cdn.invalid/image.jpg"}},
            {"type": "reply", "data": {"id": "41"}},
        ],
        "secret_field": "must not cross the boundary",
    }

    envelope = normalize_onebot_message(payload, bot_id="999")

    assert isinstance(envelope, MessageEnvelope)
    assert envelope.platform == "onebot"
    assert envelope.chat_type == "group"
    assert envelope.chat_id == "24680"
    assert envelope.sender_id == "13579"
    assert envelope.sender_name == "Member"
    assert envelope.text == "@999 请看这个 [image]"
    assert envelope.flags["mentioned"] is True
    assert envelope.flags["reply"] is True
    assert envelope.reply_to == "41"
    assert envelope.media[0].kind == "image"
    assert not hasattr(envelope, "raw_message")


def test_private_message_defaults_to_dm_and_uses_sender_as_chat_id():
    envelope = normalize_onebot_message(
        {
            "message_type": "private",
            "user_id": "13579",
            "sender": {"nickname": "Member"},
            "message": [{"type": "text", "data": {"text": "hello"}}],
        }
    )

    assert envelope is not None
    assert envelope.chat_type == "dm"
    assert envelope.chat_id == "13579"
    assert envelope.text == "hello"
    assert envelope.timestamp > 0


def test_string_form_message_extracts_text_without_cq_parameter_leakage():
    envelope = normalize_onebot_message(
        {
            "message_type": "private",
            "user_id": "13579",
            "message": "[CQ:text,text=hello%20world][CQ:image,url=https://secret.invalid/key]",
            "raw_message": "[CQ:text,text=hello%20world][CQ:image,url=https://secret.invalid/key]",
        }
    )

    assert envelope is not None
    assert envelope.text == "hello world[image]"
    assert "secret.invalid" not in envelope.text


def test_malformed_or_non_message_events_fail_closed():
    assert normalize_onebot_message(None) is None
    assert normalize_onebot_message({"post_type": "notice", "message_type": "group"}) is None
    assert normalize_onebot_message({"message_type": "group", "group_id": "1"}) is None
    assert normalize_onebot_message({"message_type": "channel", "user_id": "1"}) is None


def test_envelope_text_and_media_refs_are_bounded():
    payload = {
        "message_type": "private",
        "user_id": "1",
        "message": [
            {"type": "text", "data": {"text": "x" * (MAX_ENVELOPE_TEXT_CHARS + 100)}},
            {"type": "file", "data": {"file": "y" * 10000}},
            {"type": "unknown", "data": {"credential": "do not serialize"}},
        ],
    }
    envelope = normalize_onebot_message(payload)

    assert envelope is not None
    assert len(envelope.text) <= MAX_ENVELOPE_TEXT_CHARS
    assert len(envelope.media[0].ref) <= 2048
    assert "credential" not in envelope.text


def test_session_ref_separates_thread_and_explicit_user_scope():
    envelope = normalize_onebot_message(
        {
            "message_type": "group",
            "group_id": "24680",
            "user_id": "13579",
            "thread_id": "topic-1",
            "message": [],
        }
    )
    assert envelope is not None

    ref = session_ref_for_envelope(envelope, user_scope="13579")

    assert ref.session_key == "onebot:group:24680:thread:topic-1:user:13579"
    assert ref.session_id == ref.session_key
    assert ref.thread_id == "topic-1"


def test_delivery_intent_has_stable_hash_idempotency_key():
    envelope = normalize_onebot_message(
        {
            "message_type": "private",
            "user_id": "13579",
            "message_id": "42",
            "message": [{"type": "text", "data": {"text": "hello"}}],
        }
    )
    assert envelope is not None

    first = delivery_intent_for_envelope(
        envelope,
        session_id="session-1",
        text="reply",
    )
    second = delivery_intent_for_envelope(
        envelope,
        session_id="session-1",
        text="reply",
    )

    assert isinstance(first, DeliveryIntent)
    assert first == second
    assert len(first.idempotency_key) == 32
    assert first.idempotency_key.isalnum()


def test_capability_snapshot_defaults_to_disconnected_and_no_message_edits():
    snapshot = OneBotCapabilitySnapshot()
    assert snapshot.protocol == "onebot.v11"
    assert snapshot.connected is False
    assert snapshot.supports_message_editing is False
    assert snapshot.supports_group_mentions is True
