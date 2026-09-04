"""Offline SSRF-boundary regressions for OneBot media URLs.

All HTTP and DNS behavior is synthetic.  No external host, NapCat endpoint,
or production state database is contacted.
"""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms import base as platform_base
from plugins.platforms.onebot import adapter as adapter_module
from plugins.platforms.onebot.adapter import OneBotAdapter
from plugins.platforms.onebot.transport_contract import validate_media_url


def _adapter() -> OneBotAdapter:
    return OneBotAdapter(PlatformConfig(enabled=True, extra={}))


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.1/secret",
        "http://192.168.1.10/secret",
        "http://169.254.169.254/latest/meta-data",
        "http://100.64.0.1/internal",
        "http://[fd00::1]/internal",
        "http://2130706433/internal",
        "http://0x7f000001/internal",
        "http://017700000001/internal",
        "http://%31%32%37%2e%30%2e%30%2e%31/internal",
        r"http://127.0.0.1\@public.invalid/file",
        "http://user:secret@127.0.0.1/file",
        "http://https://127.0.0.1/file",
        "file:http://127.0.0.1/file",
        "file://remote-host/share/file.ogg",
    ],
)
def test_media_url_rejects_private_and_ambiguous_authorities(url):
    descriptor = validate_media_url(url, allow_file=False)
    assert descriptor.code in {
        "media_private_address",
        "invalid_media_url",
        "unsupported_media_scheme",
    }


def test_media_url_keeps_loopback_and_public_literal_compatibility():
    assert validate_media_url("http://127.0.0.1:3000/file", allow_file=False).code == (
        "media_url_ok"
    )
    assert validate_media_url("http://[::1]:3000/file", allow_file=False).code == (
        "media_url_ok"
    )
    # Pure URL parsing does not perform DNS; the download boundary performs
    # the subsequent public/private resolution check.
    assert validate_media_url("https://8.8.8.8/file", allow_file=False).code == (
        "media_url_ok"
    )
    assert validate_media_url("file:///C:/tmp/image.png", allow_file=True).code == (
        "media_url_ok"
    )
    assert validate_media_url("file://C:/tmp/image.png", allow_file=True).code == (
        "media_url_ok"
    )


def _addr(ip: str):
    return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


def test_download_boundary_blocks_dns_rebinding_and_resolution_failure(monkeypatch):
    adapter = _adapter()

    monkeypatch.setattr(adapter_module.socket, "getaddrinfo", lambda *args: _addr("10.0.0.1"))
    blocked = asyncio.run(adapter._validate_media_download_url("https://media.invalid/a", allow_file=False))
    assert blocked.code == "media_private_address"

    def _dns_failure(*args):
        raise socket.gaierror("synthetic resolver failure")

    monkeypatch.setattr(adapter_module.socket, "getaddrinfo", _dns_failure)
    failed = asyncio.run(adapter._validate_media_download_url("https://media.invalid/a", allow_file=False))
    assert failed.code == "media_dns_failed"


def test_download_boundary_accepts_public_dns_without_network(monkeypatch):
    adapter = _adapter()
    monkeypatch.setattr(adapter_module.socket, "getaddrinfo", lambda *args: _addr("8.8.8.8"))

    descriptor = asyncio.run(
        adapter._validate_media_download_url("https://media.invalid/a", allow_file=False)
    )

    assert descriptor.code == "media_url_ok"


def test_configured_private_onebot_host_remains_explicitly_allowed(monkeypatch):
    adapter = _adapter()
    adapter._http_url = "http://10.20.30.40:3000"
    adapter._http_endpoint = None
    monkeypatch.setattr(adapter_module.socket, "getaddrinfo", lambda *args: _addr("10.20.30.40"))

    descriptor = asyncio.run(
        adapter._validate_media_download_url(
            "http://10.20.30.40:3000/file",
            allow_file=False,
        )
    )

    assert descriptor.code == "media_url_ok"


class _Response:
    def __init__(self, status_code=200, chunks=(b"media",), headers=None):
        self.status_code = status_code
        self._chunks = list(chunks)
        self.headers = headers or {}
        self.closed = False
        self.requested_chunk_size = None

    async def aiter_bytes(self, *, chunk_size=None):
        self.requested_chunk_size = chunk_size
        for chunk in self._chunks:
            yield chunk


class _Stream:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        self.response.closed = True
        return False


class _Client:
    def __init__(self, response, calls):
        self.response = response
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def stream(self, method, url):
        self.calls.append((method, url))
        return _Stream(self.response)


def _client_factory(response, calls):
    def factory(**kwargs):
        calls.append(("client", kwargs))
        return _Client(response, calls)

    return factory


@pytest.mark.anyio
async def test_voice_redirect_is_rejected_without_following_or_cache_write(monkeypatch, tmp_path):
    response = _Response(status_code=302, headers={"location": "http://10.0.0.1/internal"})
    calls = []
    monkeypatch.setattr(adapter_module.httpx, "AsyncClient", _client_factory(response, calls))
    monkeypatch.setattr(platform_base, "get_audio_cache_dir", lambda: tmp_path)
    adapter = _adapter()

    path = await adapter._get_voice_file(
        {
            "message_id": "voice-redirect",
            "message": [{"type": "record", "data": {"url": "http://127.0.0.1:3000/voice.ogg"}}],
        }
    )

    assert path is None
    assert response.closed is True
    assert calls[0][0] == "client"
    assert calls[0][1]["follow_redirects"] is False
    assert not (tmp_path / "onebot_voice-redirect.ogg").exists()


@pytest.mark.anyio
async def test_image_redirect_is_rejected_before_cache_write(monkeypatch):
    response = _Response(status_code=302, headers={"location": "http://[::1]/internal"})
    calls = []
    monkeypatch.setattr(adapter_module.httpx, "AsyncClient", _client_factory(response, calls))
    cache = AsyncMock()
    monkeypatch.setattr(
        "gateway.platforms.base.cache_image_from_bytes",
        lambda *_args, **_kwargs: cache,
    )
    adapter = _adapter()

    paths = await adapter._get_image_files(
        {
            "message": [
                {
                    "type": "image",
                    "data": {"url": "http://127.0.0.1:3000/image.jpg"},
                }
            ]
        }
    )

    assert paths == []
    assert response.closed is True
    assert calls[0][1]["follow_redirects"] is False


@pytest.mark.anyio
async def test_get_file_url_redirect_is_rejected_but_voice_file_fallback_survives(
    monkeypatch, tmp_path
):
    redirect = _Response(status_code=302, headers={"location": "http://192.168.1.1/secret"})
    calls = []
    monkeypatch.setattr(adapter_module.httpx, "AsyncClient", _client_factory(redirect, calls))
    monkeypatch.setattr(platform_base, "get_audio_cache_dir", lambda: tmp_path)
    adapter = _adapter()
    adapter._send_action = AsyncMock(
        return_value={"data": {"file": b"fallback voice"}}
    )

    path = await adapter._get_voice_file(
        {
            "message_id": "voice-fallback",
            "message": [
                {
                    "type": "record",
                    "data": {
                        "url": "http://127.0.0.1:3000/expired.ogg",
                        "file": "voice-file-id",
                    },
                }
            ],
        }
    )

    assert path is not None
    assert path.endswith("onebot_voice-fallback.ogg")
    assert redirect.closed is True
    assert calls[0][1]["follow_redirects"] is False
    # URL failure must continue to the existing get_file fallback instead of
    # allowing a redirect or dropping the voice event as a whole.
    adapter._send_action.assert_awaited()
    assert (tmp_path / "onebot_voice-fallback.ogg").read_bytes() == b"fallback voice"


@pytest.mark.anyio
async def test_image_get_file_url_redirect_is_rejected(monkeypatch):
    response = _Response(status_code=302, headers={"location": "http://10.0.0.1/internal"})
    calls = []
    monkeypatch.setattr(adapter_module.httpx, "AsyncClient", _client_factory(response, calls))
    adapter = _adapter()
    adapter._send_action = AsyncMock(
        return_value={"data": {"file": "", "url": "http://127.0.0.1:3000/image.jpg"}}
    )

    paths = await adapter._get_image_files(
        {"message": [{"type": "image", "data": {"file": "image-file-id"}}]}
    )

    assert paths == []
    assert response.closed is True
    assert calls[0][1]["follow_redirects"] is False
    adapter._send_action.assert_awaited()


@pytest.mark.anyio
async def test_private_media_urls_fail_before_http_client_creation(monkeypatch):
    created = []

    def unexpected_client(**kwargs):
        created.append(kwargs)
        raise AssertionError("unsafe media URL must be rejected before HTTP client creation")

    monkeypatch.setattr(adapter_module.httpx, "AsyncClient", unexpected_client)
    adapter = _adapter()

    voice_path = await adapter._get_voice_file(
        {
            "message": [
                {"type": "record", "data": {"url": "http://192.168.1.10/voice.ogg"}}
            ]
        }
    )
    image_paths = await adapter._get_image_files(
        {
            "message": [
                {"type": "image", "data": {"url": "http://[fd00::1]/image.jpg"}}
            ]
        }
    )

    assert voice_path is None
    assert image_paths == []
    assert created == []


@pytest.mark.anyio
async def test_get_file_dns_rebinding_is_blocked_before_url_fetch(monkeypatch):
    created = []

    def unexpected_client(**kwargs):
        created.append(kwargs)
        raise AssertionError("DNS-rebound media URL must be rejected before HTTP client creation")

    monkeypatch.setattr(adapter_module.httpx, "AsyncClient", unexpected_client)
    monkeypatch.setattr(
        adapter_module.socket,
        "getaddrinfo",
        lambda *args: _addr("169.254.169.254"),
    )
    adapter = _adapter()
    adapter._send_action = AsyncMock(
        return_value={"data": {"file": "", "url": "https://cdn.invalid/image.jpg"}}
    )

    paths = await adapter._get_image_files(
        {"message": [{"type": "image", "data": {"file": "image-file-id"}}]}
    )

    assert paths == []
    assert created == []
    adapter._send_action.assert_awaited()
