"""Offline OneBot/NapCat transport boundary tests.

These tests use pure contracts and fake responses only. They never open a
socket, connect to NapCat, or send a QQ message.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.onebot.transport_contract import (
    MAX_MEDIA_DOWNLOAD_BYTES,
    OneBotTransportError,
    classify_http_status,
    derive_onebot_http_url,
    parse_onebot_endpoint,
    parse_onebot_receipt,
    validate_media_response,
    validate_media_url,
    validate_onebot_endpoints,
    validate_onebot_handshake,
    validate_onebot_health,
)


def test_default_napcat_endpoint_pair_uses_loopback_and_split_ports():
    ws, http = validate_onebot_endpoints(
        "ws://127.0.0.1:3001/onebot/v11/ws"
    )

    assert ws.port == 3001
    assert ws.is_loopback is True
    assert http.url == "http://127.0.0.1:3000"
    assert http.port == 3000
    assert http.is_loopback is True
    assert derive_onebot_http_url(ws.url) == http.url


def test_explicit_remote_http_endpoint_is_preserved_without_dns_lookup():
    ws, http = validate_onebot_endpoints(
        "wss://bot.example.invalid:9443/onebot",
        "https://api.example.invalid:9444/v11",
    )

    assert ws.host == "bot.example.invalid"
    assert ws.is_loopback is False
    assert http.url == "https://api.example.invalid:9444/v11"
    assert http.is_loopback is False


@pytest.mark.parametrize(
    ("url", "kind", "code"),
    [
        ("http://127.0.0.1:3001", "ws", "unsupported_scheme"),
        ("ws://user:secret@127.0.0.1:3001", "ws", "endpoint_credentials"),
        ("ws://127.0.0.1:3001/path?token=secret", "ws", "endpoint_query"),
        ("ws://127.0.0.1:0", "ws", "invalid_port"),
    ],
)
def test_endpoint_parser_rejects_unsafe_or_ambiguous_shapes(url, kind, code):
    with pytest.raises(Exception) as caught:
        parse_onebot_endpoint(url, kind=kind)

    assert getattr(caught.value, "code", None) == code


def test_handshake_and_health_contracts_are_offline_and_bounded():
    challenge = validate_onebot_handshake({"type": "auth_required"})
    accepted = validate_onebot_handshake({"type": "auth_ok"})
    rejected = validate_onebot_handshake({"type": "auth_invalid"})
    status = validate_onebot_handshake({"status": "ok", "retcode": 0})
    health = validate_onebot_health(
        {
            "status": "ok",
            "retcode": 0,
            "data": {"online": True, "good": True, "secret": "ignored"},
        }
    )

    assert challenge.state == "auth_required"
    assert challenge.authenticated is False
    assert accepted.state == "authenticated"
    assert accepted.authenticated is True
    assert rejected.state == "rejected"
    assert rejected.descriptor.code == "auth_failed"
    assert status.authenticated is True
    assert health.ok is True
    assert health.online is True
    assert health.good is True


def test_http_status_classification_keeps_auth_and_retry_policy_distinct():
    auth = classify_http_status(401)
    server = classify_http_status(503)
    send_timeout = classify_http_status(408, operation="send")
    action_timeout = classify_http_status(408, operation="action")

    assert (auth.layer, auth.code, auth.retryable) == (
        "auth",
        "http_auth_failed",
        False,
    )
    assert (server.layer, server.code, server.retryable) == (
        "endpoint",
        "http_server_error",
        True,
    )
    assert send_timeout.retryable is False
    assert action_timeout.retryable is True


def test_message_receipt_requires_success_and_message_id():
    success = parse_onebot_receipt(
        {"status": "ok", "retcode": 0, "data": {"message_id": 42}},
        require_message_id=True,
    )
    missing = parse_onebot_receipt(
        {"status": "ok", "retcode": 0, "data": {}},
        require_message_id=True,
    )
    failed = parse_onebot_receipt(
        {"status": "failed", "retcode": 100, "data": {"message_id": 99}},
        require_message_id=True,
    )

    assert success.ok is True
    assert success.message_id == "42"
    assert missing.ok is False
    assert missing.descriptor.code == "missing_receipt"
    assert failed.ok is False
    assert failed.descriptor.code == "onebot_retcode"


def test_media_contract_rejects_bad_url_status_and_oversized_body():
    assert validate_media_url("ftp://media.example.invalid/a", allow_file=False).code == (
        "unsupported_media_scheme"
    )
    assert validate_media_url("https://user:secret@media.example.invalid/a").code == (
        "invalid_media_url"
    )
    assert validate_media_response(404).code == "media_http_error"
    assert validate_media_response(
        200,
        body_length=MAX_MEDIA_DOWNLOAD_BYTES + 1,
    ).code == "media_too_large"


def test_adapter_config_defaults_and_capability_snapshot_are_offline(monkeypatch):
    from plugins.platforms.onebot.adapter import OneBotAdapter

    for name in (
        "ONEBOT_WS_URL",
        "ONEBOT_HTTP_URL",
        "ONEBOT_ACCESS_TOKEN",
        "ONEBOT_AUTO_DISCOVER_TOKEN",
        "ONEBOT_NAPCAT_CONFIG_DIR",
        "ONEBOT_REVERSE_WS_PORT",
        "ONEBOT_RECONNECT_INTERVAL",
    ):
        monkeypatch.delenv(name, raising=False)

    adapter = OneBotAdapter(
        PlatformConfig(enabled=True, extra={"auto_discover_token": False})
    )
    try:
        assert adapter._load_config() is True
        assert adapter._ws_url == "ws://127.0.0.1:3001/onebot/v11/ws"
        assert adapter._http_url == "http://127.0.0.1:3000"
        snapshot = adapter.capability_snapshot()
        assert snapshot.connected is False
        assert snapshot.ws_loopback is True
        assert snapshot.http_loopback is True
        assert snapshot.auth_configured is False
    finally:
        adapter._ws = None


def test_adapter_rejects_invalid_endpoint_without_connecting(monkeypatch):
    from plugins.platforms.onebot.adapter import OneBotAdapter

    monkeypatch.setenv("ONEBOT_WS_URL", "http://127.0.0.1:3001")
    adapter = OneBotAdapter(PlatformConfig(enabled=True, extra={}))

    assert adapter._load_config() is False
    assert adapter.fatal_error_code == "unsupported_scheme"
    assert adapter.is_connected is False


def test_send_action_classifies_http_error_without_exposing_response_body():
    from plugins.platforms.onebot.adapter import OneBotAdapter

    class FakeResponse:
        status_code = 401

        def json(self):
            return {"message": "Authorization: Bearer secret"}

    class FakeClient:
        async def post(self, *args, **kwargs):
            return FakeResponse()

    adapter = OneBotAdapter(PlatformConfig(enabled=True, extra={}))
    adapter._http_client = FakeClient()

    with pytest.raises(OneBotTransportError) as caught:
        asyncio.run(adapter._send_action("get_status", {}, timeout=0.2))

    assert caught.value.descriptor.code == "http_auth_failed"
    assert "secret" not in str(caught.value)
    assert adapter.capability_snapshot().transport_error_code == "http_auth_failed"


def test_send_requires_a_valid_message_receipt_and_does_not_retry_protocol_error():
    from plugins.platforms.onebot.adapter import OneBotAdapter

    adapter = OneBotAdapter(PlatformConfig(enabled=True, extra={}))
    adapter._ws = object()
    adapter._http_client = object()
    adapter._send_action = AsyncMock(
        return_value={"status": "ok", "retcode": 0, "data": {}}
    )

    result = asyncio.run(
        adapter._send_text_with_retry("group:24680", "hello", max_retries=3)
    )

    assert result.success is False
    assert result.retryable is False
    assert result.error == "OneBot send response did not include a message receipt"
    adapter._send_action.assert_awaited_once()


def test_send_voice_returns_send_result_when_disconnected():
    from plugins.platforms.onebot.adapter import OneBotAdapter

    adapter = OneBotAdapter(PlatformConfig(enabled=True, extra={}))
    result = asyncio.run(adapter.send_voice("24680", "voice.ogg"))

    assert result.success is False
    assert result.retryable is True


def test_websocket_connect_supports_websockets_12_extra_headers(monkeypatch):
    import plugins.platforms.onebot.adapter as adapter_module

    calls = {}

    def fake_connect(url, *, extra_headers=None, ping_interval=None, ping_timeout=None):
        calls.update(
            url=url,
            extra_headers=extra_headers,
            ping_interval=ping_interval,
            ping_timeout=ping_timeout,
        )
        return "socket-12"

    monkeypatch.setattr(adapter_module.websockets, "connect", fake_connect)

    result = adapter_module._connect_onebot_websocket(
        "ws://127.0.0.1:3001/onebot/v11/ws",
        "token-value",
    )

    assert result == "socket-12"
    assert calls == {
        "url": "ws://127.0.0.1:3001/onebot/v11/ws",
        "extra_headers": {"Authorization": "Bearer token-value"},
        "ping_interval": 15,
        "ping_timeout": 30,
    }


def test_websocket_connect_supports_websockets_15_additional_headers(monkeypatch):
    import plugins.platforms.onebot.adapter as adapter_module

    calls = {}

    def fake_connect(url, *, additional_headers=None, ping_interval=None, ping_timeout=None):
        calls.update(
            url=url,
            additional_headers=additional_headers,
            ping_interval=ping_interval,
            ping_timeout=ping_timeout,
        )
        return "socket-15"

    monkeypatch.setattr(adapter_module.websockets, "connect", fake_connect)

    result = adapter_module._connect_onebot_websocket(
        "ws://127.0.0.1:3001/onebot/v11/ws",
        "token-value",
    )

    assert result == "socket-15"
    assert calls["additional_headers"] == {"Authorization": "Bearer token-value"}
    assert "extra_headers" not in calls


def test_websocket_connect_fails_closed_when_header_api_is_unknown(monkeypatch):
    import plugins.platforms.onebot.adapter as adapter_module

    def fake_connect(*args, **kwargs):
        return "socket-unknown"

    monkeypatch.setattr(adapter_module.websockets, "connect", fake_connect)

    with pytest.raises(RuntimeError, match="no supported header parameter"):
        adapter_module._connect_onebot_websocket(
            "ws://127.0.0.1:3001/onebot/v11/ws",
            "token-value",
        )


def test_ws_loop_marks_initial_auth_rejection_as_fatal_and_disconnected():
    import json

    from plugins.platforms.onebot.adapter import OneBotAdapter

    class FakeWebSocket:
        def __init__(self):
            self.closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.closed:
                raise StopAsyncIteration
            self.closed = True
            return json.dumps(
                {
                    "status": "failed",
                    "retcode": 1403,
                    "data": None,
                    "echo": "napcat-auth",
                }
            )

        async def close(self):
            self.closed = True

    adapter = OneBotAdapter(PlatformConfig(enabled=True, extra={}))
    socket = FakeWebSocket()
    adapter._ws = socket
    adapter._ws_auth_pending = True

    asyncio.run(adapter._ws_loop())

    assert adapter.is_connected is False
    assert adapter.fatal_error_code == "ws_auth_failed"
    assert adapter.fatal_error_retryable is False
    assert adapter.capability_snapshot().transport_error_code == "ws_auth_failed"
    assert socket.closed is True
