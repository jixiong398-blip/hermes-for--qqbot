"""Gateway boundary tests for advisory error-surface descriptors."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

from gateway.config import Platform, StreamingConfig
from gateway.run import _attach_gateway_error_surface, _proxy_error_result
from gateway.run import GatewayRunner
from gateway.session import SessionSource


class _ProxyErrorResponse:
    status = 401
    content = None

    def __init__(self, error_text):
        self._error_text = error_text
        self.content = self

    async def text(self):
        return self._error_text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class _ProxyErrorSession:
    def __init__(self, response):
        self.response = response

    def post(self, *args, **kwargs):
        return self.response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


def _proxy_runner():
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner.config = MagicMock()
    runner.config.streaming = StreamingConfig()
    return runner


def _proxy_source():
    return SessionSource(
        platform=Platform.MATRIX,
        chat_id="!room:example.invalid",
        chat_name="Test Room",
        chat_type="group",
        user_id="@user:example.invalid",
        user_name="testuser",
        thread_id=None,
    )


def test_proxy_failure_has_structured_error_surface():
    result = _proxy_error_result(
        "Proxy error (401): Unauthorized: invalid API key",
        status_code=401,
    )
    assert result["failed"] is True
    assert result["error"] == result["final_response"]
    assert result["status_code"] == 401
    assert result["error_surface"] == {
        "layer": "auth",
        "code": "auth",
        "retryable": False,
        "provider": "proxy",
        "model": "hermes-agent",
        "message": "Proxy error (401): Unauthorized: invalid API key",
    }


def test_gateway_custom_endpoint_error_keeps_provider_metadata():
    result = _attach_gateway_error_surface(
        {"failed": True, "error": "request timed out"},
        provider="custom",
        model="local-model",
    )
    assert result["error_surface"]["layer"] == "endpoint"
    assert result["error_surface"]["code"] == "timeout"
    assert result["error_surface"]["provider"] == "custom"
    assert result["error_surface"]["model"] == "local-model"


def test_gateway_success_result_is_unchanged():
    result = {"final_response": "ok", "completed": True}
    assert _attach_gateway_error_surface(result) is result


def test_proxy_user_message_is_redacted_before_return(monkeypatch):
    result = _proxy_error_result(
        "Proxy error (401): Authorization: Bearer sk-1234567890abcdef"
    )
    assert "sk-1234567890abcdef" not in result["final_response"]
    assert "Authorization: Bearer" in result["final_response"]


def test_proxy_redaction_failure_uses_safe_fallback(monkeypatch):
    import agent.redact

    def _redaction_failure(*args, **kwargs):
        raise RuntimeError("redactor unavailable")

    monkeypatch.setattr(agent.redact, "redact_sensitive_text", _redaction_failure)
    result = _proxy_error_result(
        "Proxy error (401): Authorization: Bearer sk-1234567890abcdef",
        status_code=401,
    )
    assert result["final_response"] == (
        "Proxy request failed. Check the gateway logs and try again."
    )
    assert result["error"] == result["final_response"]
    assert result["status_code"] == 401


def test_proxy_http_failure_path_preserves_status_and_sanitizes_text(monkeypatch):
    monkeypatch.setenv("GATEWAY_PROXY_URL", "http://proxy.example.invalid")
    runner = _proxy_runner()
    response = _ProxyErrorResponse(
        "Unauthorized: Authorization: Bearer sk-1234567890abcdef"
    )
    session = _ProxyErrorSession(response)

    with (
        patch("aiohttp.ClientSession", return_value=session),
        patch("aiohttp.ClientTimeout"),
    ):
        result = asyncio.run(
            runner._run_agent_via_proxy(
                message="hello",
                context_prompt="",
                history=[],
                source=_proxy_source(),
                session_id="session",
            )
        )

    assert result["status_code"] == 401
    assert result["failed"] is True
    assert "sk-1234567890abcdef" not in result["final_response"]
    assert result["error_surface"]["layer"] == "auth"
