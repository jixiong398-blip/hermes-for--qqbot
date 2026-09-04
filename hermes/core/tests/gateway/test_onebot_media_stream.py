"""Offline bounded-stream tests for OneBot media downloads."""

from __future__ import annotations

import asyncio

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.onebot import adapter as adapter_module
from plugins.platforms.onebot.adapter import OneBotAdapter
from plugins.platforms.onebot.transport_contract import OneBotTransportError


class _FakeResponse:
    def __init__(self, chunks, *, status_code=200, headers=None):
        self._chunks = list(chunks)
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False
        self.requested_chunk_size = None

    async def aiter_bytes(self, *, chunk_size=None):
        self.requested_chunk_size = chunk_size
        for chunk in self._chunks:
            yield chunk


class _FakeStream:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        self.response.closed = True
        return False


class _FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def stream(self, method, url):
        self.calls.append((method, url))
        return _FakeStream(self.response)

    async def get(self, *args, **kwargs):  # pragma: no cover - regression guard
        raise AssertionError("bounded media path must use stream(), not get()")


def _run(client):
    return asyncio.run(OneBotAdapter._fetch_media_bytes(client, "http://media.invalid/a"))


def test_media_stream_rejects_oversized_content_length_before_read(monkeypatch):
    monkeypatch.setattr(adapter_module, "MAX_MEDIA_DOWNLOAD_BYTES", 8)
    response = _FakeResponse(
        [b"should-not-be-read"],
        headers={"content-length": "9"},
    )
    client = _FakeClient(response)

    with pytest.raises(OneBotTransportError) as caught:
        _run(client)

    assert caught.value.descriptor.code == "media_too_large"
    assert response.requested_chunk_size is None
    assert response.closed is True


def test_media_stream_rejects_cumulative_chunk_limit(monkeypatch):
    monkeypatch.setattr(adapter_module, "MAX_MEDIA_DOWNLOAD_BYTES", 8)
    response = _FakeResponse([b"1234", b"5678", b"9"])
    client = _FakeClient(response)

    with pytest.raises(OneBotTransportError) as caught:
        _run(client)

    assert caught.value.descriptor.code == "media_too_large"
    assert response.closed is True


def test_media_stream_returns_joined_bytes_and_headers(monkeypatch):
    monkeypatch.setattr(adapter_module, "MAX_MEDIA_DOWNLOAD_BYTES", 16)
    response = _FakeResponse(
        [b"hello", bytearray(b" "), memoryview(b"world")],
        headers={"content-type": "image/png"},
    )
    client = _FakeClient(response)

    content, headers = _run(client)

    assert content == b"hello world"
    assert headers["content-type"] == "image/png"
    assert response.requested_chunk_size == 64 * 1024
    assert response.closed is True
    assert client.calls == [("GET", "http://media.invalid/a")]


def test_media_stream_rejects_http_error_without_reading_body(monkeypatch):
    monkeypatch.setattr(adapter_module, "MAX_MEDIA_DOWNLOAD_BYTES", 8)
    response = _FakeResponse([b"error-body"], status_code=404)
    client = _FakeClient(response)

    with pytest.raises(OneBotTransportError) as caught:
        _run(client)

    assert caught.value.descriptor.code == "media_http_error"
    assert response.requested_chunk_size is None
    assert response.closed is True


def test_voice_download_uses_streamed_bytes_for_cache_write(monkeypatch, tmp_path):
    """The voice path must use the helper result, not the old ``resp`` object."""
    response = _FakeResponse(
        [b"voice-bytes"],
        headers={"content-type": "audio/ogg"},
    )
    client = _FakeClient(response)

    class _AsyncClientContext:
        async def __aenter__(self):
            return client

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        adapter_module.httpx,
        "AsyncClient",
        lambda **kwargs: _AsyncClientContext(),
    )
    from gateway.platforms import base as platform_base

    monkeypatch.setattr(platform_base, "get_audio_cache_dir", lambda: tmp_path)
    adapter = OneBotAdapter(PlatformConfig(enabled=True, extra={}))

    path = asyncio.run(
        adapter._get_voice_file(
            {
                "message_id": "voice-stream-1",
                "message": [
                    {
                        "type": "record",
                        "data": {"url": "http://127.0.0.1:3000/voice.ogg"},
                    }
                ],
            }
        )
    )

    assert path is not None
    assert (tmp_path / "onebot_voice-stream-1.ogg").read_bytes() == b"voice-bytes"
    assert response.closed is True
