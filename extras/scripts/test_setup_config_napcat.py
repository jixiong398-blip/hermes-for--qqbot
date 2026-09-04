"""Contract tests for setup_config's account-specific NapCat discovery."""

from __future__ import annotations

import json
from pathlib import Path

import extras.scripts.setup_config as setup_config
import extras.scripts.upgrade as upgrade_script


def _write_account(directory, account_id, token):
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


def test_auto_read_uses_login_marker_and_ignores_base_template(tmp_path, monkeypatch):
    _write_account(tmp_path, "111", "old-token")
    _write_account(tmp_path, "222", "active-token")
    (tmp_path / "onebot11.json").write_text(
        json.dumps({"network": {"httpServers": [{"enable": True, "token": "base-token"}]}}),
        encoding="utf-8",
    )
    old_marker = tmp_path / "napcat_protocol_111.json"
    active_marker = tmp_path / "napcat_protocol_222.json"
    old_marker.write_text("{}", encoding="utf-8")
    active_marker.write_text("{}", encoding="utf-8")
    old_marker.touch()
    active_marker.touch()
    old_marker_mtime = 100
    active_marker_mtime = 200
    import os

    os.utime(old_marker, (old_marker_mtime, old_marker_mtime))
    os.utime(active_marker, (active_marker_mtime, active_marker_mtime))

    monkeypatch.setattr(setup_config, "BOT_DIR", tmp_path.parent)
    monkeypatch.setenv("ONEBOT_SELF_ID", "")
    (tmp_path.parent / "modules" / "napcat" / "napcat" / "config").mkdir(parents=True)
    # Point the real relative lookup at the fixture directory.
    fixture_root = tmp_path.parent / "modules" / "napcat" / "napcat" / "config"
    for source in tmp_path.iterdir():
        source.replace(fixture_root / source.name)

    token, account_id = setup_config.auto_read_napcat_credentials()

    assert token == "active-token"
    assert account_id == "222"


def test_generate_env_replaces_selected_self_id(tmp_path, monkeypatch):
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / ".env.template").write_text(
        "ONEBOT_ACCESS_TOKEN={{ONEBOT_ACCESS_TOKEN}}\n"
        "ONEBOT_SELF_ID={{ONEBOT_SELF_ID}}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(setup_config, "TPL_DIR", template_dir)
    generated = setup_config.generate_env(
        "llm", "vision", "search", "gateway", {}, "knowledge", "admin", "bot"
    )

    assert "ONEBOT_SELF_ID=bot" in generated


def test_upgrade_map_copies_agent_runtime_contract():
    upgrade_source = (Path(__file__).with_name("upgrade.py")).read_text(
        encoding="utf-8"
    )

    assert '("hermes/core/run_agent.py", "hermes/core/run_agent.py")' in upgrade_source
    assert '("hermes/core/agent/provider_projection.py", "hermes/core/agent/provider_projection.py")' in upgrade_source


def test_upgrade_map_copies_onebot_imported_contract_modules():
    upgrade_source = (Path(__file__).with_name("upgrade.py")).read_text(
        encoding="utf-8"
    )

    for module in ("config_discovery.py", "contract.py", "transport_contract.py"):
        entry = (
            f'("hermes/core/plugins/platforms/onebot/{module}", '
            f'"hermes/core/plugins/platforms/onebot/{module}")'
        )
        assert entry in upgrade_source

    assert '("hermes/core/plugins/platforms/onebot/plugin.yaml", "hermes/core/plugins/platforms/onebot/plugin.yaml")' in upgrade_source


def test_upgrade_map_covers_new_runtime_dependency_ports():
    upgrade_source = (Path(__file__).with_name("upgrade.py")).read_text(
        encoding="utf-8"
    )

    required = (
        "hermes/core/gateway/config.py",
        "hermes/core/gateway/session.py",
        "hermes/core/gateway/platforms/base.py",
        "hermes/core/gateway/delivery_ledger.py",
        "hermes/core/gateway/session_stall.py",
        "hermes/core/gateway/shutdown_flush.py",
        "hermes/core/gateway/turn_lease.py",
        "hermes/core/agent/empty_response_guard.py",
        "hermes/core/agent/error_surface.py",
        "hermes/core/agent/errors.py",
        "hermes/core/agent/repetition_guard.py",
        "hermes/core/agent/session_activity.py",
        "hermes/core/agent/message_sanitization.py",
        "hermes/core/hermes_state_common.py",
        "hermes/core/hermes_state_common_compat.py",
        "hermes/core/hermes_state_portability.py",
        "hermes/core/hermes_state_portability_compat.py",
        "hermes/core/hermes_state_replay.py",
        "hermes/core/hermes_state_schema.py",
        "hermes/core/hermes_state_schema_probe.py",
        "hermes/core/hermes_state_search.py",
        "hermes/core/hermes_state_v26_compat.py",
        "hermes/core/scripts/sessiondb_replay.py",
        "hermes/core/tools/environments/contract.py",
        "hermes/core/tools/spill_safety.py",
    )
    for path in required:
        assert f'("{path}", "{path}")' in upgrade_source


def test_upgrade_dual_writes_onebot_imported_contract_modules(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    template_root = tmp_path / "template"
    home_root = tmp_path / "home"
    modules = ("config_discovery.py", "contract.py", "transport_contract.py")
    for module in modules:
        source_file = (
            source_root / "hermes" / "core" / "plugins" / "platforms" / "onebot" / module
        )
        source_file.parent.mkdir(parents=True, exist_ok=True)
        source_file.write_text(f"# fixture {module}\n", encoding="utf-8")

    monkeypatch.setattr(upgrade_script, "BOT_DIR", template_root)
    monkeypatch.setattr(upgrade_script, "HERMES_HOME", home_root)
    monkeypatch.setattr(
        upgrade_script,
        "UPGRADE_MAP",
        [
            (
                f"hermes/core/plugins/platforms/onebot/{module}",
                f"hermes/core/plugins/platforms/onebot/{module}",
            )
            for module in modules
        ],
    )

    updated = upgrade_script.upgrade(str(source_root))

    assert len(updated) == len(modules)
    for module in modules:
        expected = f"# fixture {module}\n"
        assert (
            home_root / "plugins" / "platforms" / "onebot" / module
        ).read_text(encoding="utf-8") == expected
        assert (
            template_root / "hermes" / "core" / "plugins" / "platforms" / "onebot" / module
        ).read_text(encoding="utf-8") == expected


def test_upgrade_closes_runtime_python_import_graph_without_tests_or_docs(
    tmp_path, monkeypatch
):
    source_root = tmp_path / "source"
    core = source_root / "hermes" / "core"
    (core / "agent").mkdir(parents=True)
    (core / "tests").mkdir(parents=True)
    (core / "docs").mkdir(parents=True)
    (core / "agent" / "__init__.py").write_text("", encoding="utf-8")
    (core / "agent" / "runtime_port.py").write_text(
        "from agent.optional_port import value\n", encoding="utf-8"
    )
    (core / "agent" / "optional_port.py").write_text(
        "value = 1\n", encoding="utf-8"
    )
    (core / "tests" / "test_only.py").write_text("raise RuntimeError\n", encoding="utf-8")
    (core / "docs" / "example.py").write_text("raise RuntimeError\n", encoding="utf-8")

    template_root = tmp_path / "template"
    home_root = tmp_path / "home"
    monkeypatch.setattr(upgrade_script, "BOT_DIR", template_root)
    monkeypatch.setattr(upgrade_script, "HERMES_HOME", home_root)
    monkeypatch.setattr(upgrade_script, "UPGRADE_MAP", [])

    updated = upgrade_script.upgrade(str(source_root))

    assert any("agent/runtime_port.py" in item for item in updated)
    assert (home_root / "agent" / "runtime_port.py").is_file()
    assert (home_root / "agent" / "optional_port.py").is_file()
    assert not (home_root / "tests" / "test_only.py").exists()
    assert not (home_root / "docs" / "example.py").exists()


def test_upgrade_rejects_traversal_entries_before_copy(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    source_root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("outside\n", encoding="utf-8")
    template_root = tmp_path / "template"
    home_root = tmp_path / "home"
    monkeypatch.setattr(upgrade_script, "BOT_DIR", template_root)
    monkeypatch.setattr(upgrade_script, "HERMES_HOME", home_root)
    monkeypatch.setattr(
        upgrade_script,
        "UPGRADE_MAP",
        [("../outside.py", "../escaped.py")],
    )

    updated = upgrade_script.upgrade(str(source_root))

    assert updated == []
    assert not (home_root.parent / "escaped.py").exists()
    assert not (template_root.parent / "escaped.py").exists()


def test_dynamic_core_file_limit_stops_incremental_enumeration(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    core = source_root / "hermes" / "core"
    core.mkdir(parents=True)
    for name in ("a.py", "b.py", "c.py"):
        (core / name).write_text("value = 1\n", encoding="utf-8")

    monkeypatch.setattr(upgrade_script, "UPGRADE_MAP", [])
    monkeypatch.setattr(upgrade_script, "MAX_DYNAMIC_CORE_FILES", 2)

    entries = list(upgrade_script._iter_dynamic_core_entries(source_root))

    assert len(entries) == 2


def test_upgrade_rejects_destination_symlink(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    source_file = source_root / "hermes" / "core" / "safe.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("safe\n", encoding="utf-8")
    template_root = tmp_path / "template"
    home_root = tmp_path / "home"
    outside = tmp_path / "outside"
    outside.mkdir()
    home_root.mkdir()
    link = home_root / "agent"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks unavailable on this platform")

    monkeypatch.setattr(upgrade_script, "BOT_DIR", template_root)
    monkeypatch.setattr(upgrade_script, "HERMES_HOME", home_root)
    monkeypatch.setattr(
        upgrade_script,
        "UPGRADE_MAP",
        [("hermes/core/safe.py", "hermes/core/agent/safe.py")],
    )

    updated = upgrade_script.upgrade(str(source_root))

    assert updated == []
    assert not (outside / "safe.py").exists()


def test_upgrade_dry_run_is_read_only_and_validates_full_plan(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    source_file = source_root / "hermes" / "core" / "agent" / "dry.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("dry\n", encoding="utf-8")
    template_root = tmp_path / "template"
    home_root = tmp_path / "home"
    home_root.mkdir()
    sentinel = home_root / "config.yaml"
    sentinel.write_text("keep\n", encoding="utf-8")

    monkeypatch.setattr(upgrade_script, "BOT_DIR", template_root)
    monkeypatch.setattr(upgrade_script, "HERMES_HOME", home_root)
    monkeypatch.setattr(
        upgrade_script,
        "UPGRADE_MAP",
        [("hermes/core/agent/dry.py", "hermes/core/agent/dry.py")],
    )

    updated = upgrade_script.upgrade(str(source_root), dry_run=True)

    assert updated == ["hermes/core/agent/dry.py -> ~/.hermes/agent/dry.py"]
    assert sentinel.read_text(encoding="utf-8") == "keep\n"
    assert not (home_root / "agent" / "dry.py").exists()
    assert not (template_root / "hermes" / "core" / "agent" / "dry.py").exists()


def test_upgrade_dual_writes_agent_runtime_contract(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    source_file = source_root / "hermes" / "core" / "run_agent.py"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("# fixture runtime\n", encoding="utf-8")

    template_root = tmp_path / "template"
    home_root = tmp_path / "home"
    monkeypatch.setattr(upgrade_script, "BOT_DIR", template_root)
    monkeypatch.setattr(upgrade_script, "HERMES_HOME", home_root)
    monkeypatch.setattr(
        upgrade_script,
        "UPGRADE_MAP",
        [("hermes/core/run_agent.py", "hermes/core/run_agent.py")],
    )

    updated = upgrade_script.upgrade(str(source_root))

    assert updated == ["hermes/core/run_agent.py -> ~/.hermes/run_agent.py"]
    assert (home_root / "run_agent.py").read_text(encoding="utf-8") == "# fixture runtime\n"
    assert (template_root / "hermes" / "core" / "run_agent.py").read_text(
        encoding="utf-8"
    ) == "# fixture runtime\n"
