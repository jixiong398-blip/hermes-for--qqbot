"""Offline tests for the first-run installer path."""

from __future__ import annotations

from pathlib import Path

import extras.scripts.install as install_script


def test_installer_resolves_distribution_root():
    root = Path(__file__).resolve().parents[2]

    assert install_script.BOT_DIR == root
    assert (install_script.BOT_DIR / "install.bat").is_file()
    assert (install_script.BOT_DIR / "hermes" / "core").is_dir()


def test_setup_creates_config_env_and_knowledge_dir(tmp_path, monkeypatch):
    bot_root = tmp_path / "bot"
    (bot_root / "hermes" / "core").mkdir(parents=True)
    (bot_root / "templates").mkdir()
    (bot_root / "modules").mkdir()
    (bot_root / "install.bat").write_text("@echo off\n", encoding="utf-8")
    (bot_root / "templates" / "SOUL.md").write_text(
        "# SOUL.md — FixtureBot\n", encoding="utf-8"
    )
    (bot_root / "templates" / "config-template.yaml").write_text(
        "model:\n  model: fixture\n", encoding="utf-8"
    )
    home = tmp_path / "home"
    monkeypatch.setattr(install_script, "BOT_DIR", bot_root)
    monkeypatch.setattr(install_script, "HERMES_HOME", home)

    install_script.setup()

    env = (home / ".env").read_text(encoding="utf-8")
    assert "ONEBOT_BOT_NAME=FixtureBot" in env
    assert "ONEBOT_AUTO_DISCOVER_TOKEN=true" in env
    assert (home / "config.yaml").read_text(encoding="utf-8") == (
        "model:\n  model: fixture\n"
    )
    assert (bot_root / "modules" / "knowledge" / ".gitkeep").is_file()
