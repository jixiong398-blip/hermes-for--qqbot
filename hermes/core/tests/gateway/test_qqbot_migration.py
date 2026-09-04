"""Regression tests for the QQBot -> OneBot migration boundary."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig, _apply_env_overrides
from gateway.run import GatewayRunner
from tools.send_message_tool import _parse_target_ref, _send_qqbot


def test_legacy_qqbot_never_reports_connected():
    config = GatewayConfig(
        platforms={
            Platform.QQBOT: PlatformConfig(
                enabled=True,
                token="legacy-secret",
                extra={"app_id": "legacy-app", "client_secret": "legacy-secret"},
            )
        }
    )

    assert Platform.QQBOT not in config.get_connected_platforms()
    assert config._is_platform_connected(
        Platform.QQBOT, config.platforms[Platform.QQBOT]
    ) is False


def test_legacy_qqbot_env_is_disabled_with_migration_warning(monkeypatch, caplog):
    monkeypatch.setenv("QQ_APP_ID", "legacy-app")
    monkeypatch.setenv("QQ_CLIENT_SECRET", "legacy-secret")
    config = GatewayConfig()

    with caplog.at_level("WARNING"):
        _apply_env_overrides(config)

    legacy = config.platforms[Platform.QQBOT]
    assert legacy.enabled is False
    assert legacy.extra["legacy_configured"] is True
    assert Platform.QQBOT not in config.get_connected_platforms()
    assert "OneBot/NapCat" in caplog.text


def test_gateway_does_not_create_removed_qqbot_adapter(monkeypatch):
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = GatewayConfig()
    runner.adapters = {}

    assert runner._create_adapter(
        Platform.QQBOT,
        PlatformConfig(enabled=True, extra={"app_id": "legacy", "client_secret": "legacy"}),
    ) is None


def test_legacy_direct_sender_never_contacts_official_api(monkeypatch):
    class FailClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("removed QQBot REST API must not be contacted")

    monkeypatch.setattr("httpx.AsyncClient", FailClient)
    result = asyncio.run(
        _send_qqbot(
            SimpleNamespace(extra={"app_id": "legacy"}, token="legacy"),
            "123",
            "hello",
        )
    )

    assert result["error"]
    assert "removed" in result["error"].lower()


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("group:123456", ("group:123456", None, True)),
        ("private:123456", ("123456", None, True)),
        ("123456", ("123456", None, True)),
    ],
)
def test_onebot_target_refs_are_explicit(target, expected):
    assert _parse_target_ref("onebot", target) == expected


def test_cron_resolves_onebot_home_and_rejects_qqbot(monkeypatch):
    from cron.scheduler import _resolve_single_delivery_target

    monkeypatch.setenv("ONEBOT_HOME_CHANNEL", "group:123")
    assert _resolve_single_delivery_target(
        {"id": "job-onebot"}, "onebot"
    ) == {
        "platform": "onebot",
        "chat_id": "group:123",
        "thread_id": None,
    }
    assert _resolve_single_delivery_target({"id": "job-qq"}, "qqbot") is None


def test_onebot_plugin_seeds_home_channel_from_environment(monkeypatch):
    from plugins.platforms.onebot.adapter import _env_enablement

    monkeypatch.setenv("ONEBOT_WS_URL", "ws://127.0.0.1:3001/")
    monkeypatch.setenv("ONEBOT_HOME_CHANNEL", "group:123")
    seed = _env_enablement()

    assert seed["extra"]["ws_url"].endswith("/")
    assert seed["home_channel"]["chat_id"] == "group:123"


def test_user_facing_platform_registry_has_onebot_without_qqbot():
    from hermes_cli.platforms import get_all_platforms

    platforms = get_all_platforms()
    assert "qqbot" not in platforms
