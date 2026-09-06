"""Dashboard account-selector contract tests.

All NapCat files here are temporary fixtures; no real token or service is
accessed.
"""

from __future__ import annotations

import json
from pathlib import Path

import modules.dashboard.server as server


def _write_config(directory: Path, account_id: str, token: str) -> None:
    payload = {
        "network": {
            "httpServers": [
                {"enable": True, "host": "127.0.0.1", "port": 3000, "token": token}
            ],
            "websocketServers": [
                {"enable": True, "host": "127.0.0.1", "port": 3001, "token": token}
            ],
        }
    }
    (directory / f"onebot11_{account_id}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    (directory / f"napcat_protocol_{account_id}.json").write_text(
        "{}", encoding="utf-8"
    )


class _FakeHandler:
    def __init__(self):
        self.data = None
        self.status = None

    def _send_json(self, data, status=200):
        self.data = data
        self.status = status


def test_account_list_is_redacted_and_selection_writes_only_selector_values(
    tmp_path, monkeypatch
):
    config_dir = tmp_path / "napcat-config"
    config_dir.mkdir()
    _write_config(config_dir, "222", "fixture-secret")

    monkeypatch.setattr(server, "NAPCAT_CONFIG_DIR", config_dir)
    monkeypatch.setattr(server, "HERMES_HOME", tmp_path / "hermes-home")
    monkeypatch.setattr(server, "_check_gateway_process", lambda: False)

    listed = _FakeHandler()
    server.DashboardHandler._handle_napcat_accounts(listed)
    selected = _FakeHandler()
    server.DashboardHandler._handle_napcat_account_select(
        selected, {"account_id": "222"}
    )

    assert listed.status == 200
    assert listed.data["accounts"][0]["account_id"] == "222"
    assert "fixture-secret" not in json.dumps(listed.data)
    assert selected.status == 200
    assert selected.data["success"] is True
    env = (server.HERMES_HOME / ".env").read_text(encoding="utf-8")
    assert "ONEBOT_SELF_ID=222" in env
    assert "ONEBOT_AUTO_DISCOVER_TOKEN=true" in env
    assert "fixture-secret" not in env


def test_account_selection_rejects_unknown_or_malformed_ids(tmp_path, monkeypatch):
    config_dir = tmp_path / "napcat-config"
    config_dir.mkdir()
    _write_config(config_dir, "222", "fixture-secret")
    monkeypatch.setattr(server, "NAPCAT_CONFIG_DIR", config_dir)
    monkeypatch.setattr(server, "HERMES_HOME", tmp_path / "hermes-home")

    for body in ({"account_id": "999"}, {"account_id": "../222"}, {"account_id": True}):
        handler = _FakeHandler()
        server.DashboardHandler._handle_napcat_account_select(handler, body)
        assert handler.status in {400, 409}
        assert handler.data["success"] is False


def test_dashboard_uses_the_distribution_napcat_directory():
    assert server.NAPCAT_DIR.name == "napcat"
    assert server.SERVICES["napcat"]["cwd"] == str(server.NAPCAT_DIR)


def test_napcat_preflight_rejects_missing_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "NAPCAT_DIR", tmp_path / "missing-napcat")
    handler = object.__new__(server.DashboardHandler)

    failure = handler._napcat_preflight()

    assert failure["code"] == "invalid_cwd"
    assert "重新安装" in failure["hint"]


def test_napcat_preflight_rejects_running_qq(tmp_path, monkeypatch):
    napcat_dir = tmp_path / "napcat"
    napcat_dir.mkdir()
    (napcat_dir / "napcat.bat").write_text("@echo off\n", encoding="utf-8")
    monkeypatch.setattr(server, "NAPCAT_DIR", napcat_dir)
    monkeypatch.setattr(server.DashboardHandler, "_find_running_qq", staticmethod(lambda: ['"QQ.exe"']))

    handler = object.__new__(server.DashboardHandler)
    failure = handler._napcat_preflight()

    assert failure["code"] == "qq_client_busy"
    assert "不会自动结束 QQ" in failure["hint"]


def test_napcat_stop_targets_process_tree_and_all_ports(monkeypatch):
    calls = []

    class _Completed:
        returncode = 0

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return _Completed()

    monkeypatch.setattr(server.os, "name", "nt")
    monkeypatch.setattr(server.subprocess, "run", fake_run)
    handler = object.__new__(server.DashboardHandler)
    handler._send_json = lambda data, status=200: setattr(handler, "response", (data, status))
    handler._handle_napcat_stop()

    assert len(calls) == 1
    script = calls[0][0][-1]
    assert handler.response == ({"success": True, "message": "NapCat stopped"}, 200)
    assert "$self = $PID" in script
    assert "3000, 3001, 3002, 6099" in script
    assert "taskkill /F /T /PID" in script
    assert "if ($ids.Contains($ppid) -and $ids.Add($pid))" in script
