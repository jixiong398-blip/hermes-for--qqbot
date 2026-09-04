"""Tests for bounded NapCat account-specific OneBot credential discovery."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.onebot.config_discovery import (
    discover_napcat_onebot_credentials,
)


def _write_config(
    directory: Path,
    account_id: str,
    token: str,
    *,
    http_token: str | None = None,
    websocket_token: str | None = None,
    http_host: str = "127.0.0.1",
    websocket_host: str = "127.0.0.1",
) -> Path:
    payload = {
        "network": {
            "httpServers": [
                {
                    "enable": True,
                    "host": http_host,
                    "port": 3000,
                    "token": http_token if http_token is not None else token,
                }
            ],
            "websocketServers": [
                {
                    "enable": True,
                    "host": websocket_host,
                    "port": 3001,
                    "token": websocket_token if websocket_token is not None else token,
                }
            ],
        }
    }
    path = directory / f"onebot11_{account_id}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_explicit_self_id_selects_exact_account_file(tmp_path):
    _write_config(tmp_path, "111", "token-111")
    _write_config(tmp_path, "222", "token-222")

    credentials = discover_napcat_onebot_credentials("222", config_dir=tmp_path)

    assert credentials is not None
    assert credentials.account_id == "222"
    assert credentials.token == "token-222"
    assert credentials.http_port == 3000
    assert credentials.websocket_port == 3001


def test_newest_login_marker_selects_account_when_self_id_is_missing(tmp_path):
    older = _write_config(tmp_path, "111", "token-111")
    newer = _write_config(tmp_path, "222", "token-222")
    protocol_old = tmp_path / "napcat_protocol_111.json"
    protocol_new = tmp_path / "napcat_protocol_222.json"
    protocol_old.write_text("{}", encoding="utf-8")
    protocol_new.write_text("{}", encoding="utf-8")
    os.utime(protocol_old, (100, 100))
    os.utime(protocol_new, (200, 200))
    os.utime(older, (300, 300))
    os.utime(newer, (100, 100))

    credentials = discover_napcat_onebot_credentials(config_dir=tmp_path)

    assert credentials is not None
    assert credentials.account_id == "222"
    assert credentials.token == "token-222"


def test_explicit_missing_self_id_does_not_fall_through_to_other_account(tmp_path):
    _write_config(tmp_path, "111", "token-111")

    assert discover_napcat_onebot_credentials("999", config_dir=tmp_path) is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"http_token": "http", "websocket_token": "websocket"},
        {"http_host": "192.0.2.10"},
        {"websocket_host": "192.0.2.10"},
    ],
)
def test_discovery_rejects_ambiguous_or_non_loopback_servers(tmp_path, kwargs):
    _write_config(tmp_path, "111", "token-111", **kwargs)

    assert discover_napcat_onebot_credentials("111", config_dir=tmp_path) is None


def test_adapter_load_config_discovers_current_account_and_replaces_stale_env(
    tmp_path, monkeypatch
):
    _write_config(tmp_path, "222", "fresh-token")
    protocol = tmp_path / "napcat_protocol_222.json"
    protocol.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("ONEBOT_WS_URL", "ws://127.0.0.1:3001/")
    monkeypatch.setenv("ONEBOT_HTTP_URL", "http://127.0.0.1:3000")
    monkeypatch.setenv("ONEBOT_ACCESS_TOKEN", "stale-token")
    monkeypatch.delenv("ONEBOT_SELF_ID", raising=False)
    monkeypatch.setenv("ONEBOT_NAPCAT_CONFIG_DIR", str(tmp_path))

    from plugins.platforms.onebot.adapter import OneBotAdapter

    adapter = OneBotAdapter(PlatformConfig(enabled=True, extra={}))

    assert adapter._load_config() is True
    assert adapter._access_token == "fresh-token"
    assert adapter._self_id == 222


def test_adapter_can_disable_auto_discovery(tmp_path, monkeypatch):
    _write_config(tmp_path, "222", "fresh-token")
    monkeypatch.setenv("ONEBOT_WS_URL", "ws://127.0.0.1:3001/")
    monkeypatch.setenv("ONEBOT_HTTP_URL", "http://127.0.0.1:3000")
    monkeypatch.setenv("ONEBOT_ACCESS_TOKEN", "stale-token")
    monkeypatch.delenv("ONEBOT_SELF_ID", raising=False)
    monkeypatch.setenv("ONEBOT_NAPCAT_CONFIG_DIR", str(tmp_path))

    from plugins.platforms.onebot.adapter import OneBotAdapter

    adapter = OneBotAdapter(
        PlatformConfig(enabled=True, extra={"auto_discover_token": False})
    )

    assert adapter._load_config() is True
    assert adapter._access_token == "stale-token"
    assert adapter._self_id is None


def test_onebot_manifest_keeps_local_napcat_values_optional():
    manifest = (
        Path(__file__).resolve().parents[2]
        / "plugins"
        / "platforms"
        / "onebot"
        / "plugin.yaml"
    ).read_text(encoding="utf-8")

    assert "requires_env: []" in manifest
    optional_section = manifest.split("optional_env:", 1)[1]
    assert "name: ONEBOT_WS_URL" in optional_section
    assert "name: ONEBOT_HTTP_URL" in optional_section
    assert "name: ONEBOT_ACCESS_TOKEN" in optional_section
    assert manifest.count("name: ONEBOT_WS_URL") == 1
    assert manifest.count("name: ONEBOT_HTTP_URL") == 1
    assert manifest.count("name: ONEBOT_ACCESS_TOKEN") == 1
