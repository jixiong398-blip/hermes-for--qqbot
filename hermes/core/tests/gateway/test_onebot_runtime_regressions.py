from unittest.mock import AsyncMock, MagicMock

import anyio

from gateway.config import GatewayConfig, HomeChannel, Platform, PlatformConfig
from gateway.platforms.base import MessageType
from gateway.session import SessionSource


def test_home_channel_prompt_skips_onebot_config_home_channel(monkeypatch):
    from gateway.run import GatewayRunner

    onebot = Platform("onebot")
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={
            onebot: PlatformConfig(
                enabled=True,
                home_channel=HomeChannel(
                    platform=onebot,
                    chat_id="10001",
                    name="Home",
                ),
            ),
        },
    )
    source = SessionSource(platform=onebot, chat_id="10001")
    monkeypatch.delenv("ONEBOT_HOME_CHANNEL", raising=False)

    assert GatewayRunner._should_send_home_channel_prompt(runner, source) is False


def test_home_channel_prompt_runs_when_onebot_has_no_home(monkeypatch):
    from gateway.run import GatewayRunner

    onebot = Platform("onebot")
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={onebot: PlatformConfig(enabled=True)},
    )
    source = SessionSource(platform=onebot, chat_id="10001")
    monkeypatch.delenv("ONEBOT_HOME_CHANNEL", raising=False)

    assert GatewayRunner._should_send_home_channel_prompt(runner, source) is True


def test_onebot_voice_uses_mimo_transcript_before_gateway_stt():
    from plugins.platforms.onebot.adapter import OneBotAdapter

    adapter = OneBotAdapter(PlatformConfig(enabled=True, extra={}))
    adapter._persist_chat_message = MagicMock()
    adapter._recall_context = MagicMock(return_value="")
    adapter._get_voice_file = AsyncMock(return_value="/tmp/onebot_voice.ogg")
    adapter._transcribe_voice_mimo = AsyncMock(return_value="晚上好")
    adapter.handle_message = AsyncMock()

    anyio.run(
        adapter._process_message_impl,
        {
            "user_id": 10001,
            "self_id": 10086,
            "message_type": "private",
            "message_id": "voice-1",
            "time": 1760000000,
            "sender": {"nickname": "Tester"},
            "raw_message": "",
            "message": [{"type": "record", "data": {"file": "voice-1"}}],
        },
    )

    adapter.handle_message.assert_awaited_once()
    event = adapter.handle_message.await_args.args[0]
    assert event.message_type == MessageType.VOICE
    assert "[语音转写: 晚上好]" in event.text
    assert event.media_urls == []
    assert event.media_types == []

    # Corpus/buffer persistence must carry transcript (mirrors image branch
    # where _describe_image result is appended to m_text before persist).
    voice_persist = [c for c in adapter._persist_chat_message.call_args_list
                     if (c.args[1] if len(c.args) > 1 else c.kwargs.get("chat_type")) == "private"]
    assert voice_persist, "_persist_chat_message was not called for the voice branch"
    args, kwargs = voice_persist[-1]
    persisted_text = args[4] if len(args) > 4 else kwargs.get("content", "")
    assert "[语音转写: 晚上好]" in persisted_text
    descs = kwargs.get("image_descriptions") or []
    assert any("晚上好" in d for d in descs)


def test_onebot_voice_keeps_gateway_stt_fallback_when_mimo_has_no_text():
    from plugins.platforms.onebot.adapter import OneBotAdapter

    adapter = OneBotAdapter(PlatformConfig(enabled=True, extra={}))
    adapter._persist_chat_message = MagicMock()
    adapter._recall_context = MagicMock(return_value="")
    adapter._get_voice_file = AsyncMock(return_value="/tmp/onebot_voice.ogg")
    adapter._transcribe_voice_mimo = AsyncMock(return_value="语音")
    adapter.handle_message = AsyncMock()

    anyio.run(
        adapter._process_message_impl,
        {
            "user_id": 10001,
            "self_id": 10086,
            "message_type": "private",
            "message_id": "voice-2",
            "time": 1760000000,
            "sender": {"nickname": "Tester"},
            "raw_message": "",
            "message": [{"type": "record", "data": {"file": "voice-2"}}],
        },
    )

    adapter.handle_message.assert_awaited_once()
    event = adapter.handle_message.await_args.args[0]
    assert event.message_type == MessageType.VOICE
    assert event.text == ""
    assert event.media_urls == ["/tmp/onebot_voice.ogg"]
    assert event.media_types == ["audio/ogg"]

    voice_persist = [c for c in adapter._persist_chat_message.call_args_list
                     if (c.args[1] if len(c.args) > 1 else c.kwargs.get("chat_type")) == "private"]
    assert voice_persist, "_persist_chat_message was not called for the voice branch"
    args, kwargs = voice_persist[-1]
    persisted_text = args[4] if len(args) > 4 else kwargs.get("content", "")
    assert "[语音]" in persisted_text
    assert "[语音转写" not in persisted_text
