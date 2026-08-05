#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QQBot Upgrade Script — v0.5.2+

Usage:
    python scripts/upgrade.py [source_dir]

This script applies the latest changes to an existing QQBot installation.
It copies updated Python source files, configuration templates, and scripts
while preserving user-modified configs (config.yaml, SOUL.md, .env).

For AI agents: call this with the bot-template root as source_dir.
For humans: run from the bot-template directory without arguments.
"""
import os, shutil, sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
# BOT_DIR = bot-template root: walk up from SCRIPT_DIR until we find a dir
# containing install.bat. Script moved from scripts/ -> extras/scripts in
# v0.14.3, so SCRIPT_DIR.parent alone is no longer the root.
BOT_DIR = SCRIPT_DIR
for _ in range(4):
    if (BOT_DIR / "install.bat").exists():
        break
    BOT_DIR = BOT_DIR.parent
HERMES_HOME = Path.home() / ".hermes"

# Files to upgrade (source -> destination relative to BOT_DIR)
UPGRADE_MAP = [
    # Hermes core — gateway
    ("hermes/core/gateway/run.py", "hermes/core/gateway/run.py"),
    ("hermes/core/gateway/platforms/onebot/adapter.py", "hermes/core/gateway/platforms/onebot/adapter.py"),
    # Hermes plugins — OneBot (v0.10.5+ three-phase pipeline)
    ("hermes/core/plugins/platforms/onebot/adapter.py", "hermes/core/plugins/platforms/onebot/adapter.py"),
    ("hermes/core/plugins/platforms/onebot/semantic_judge.py", "hermes/core/plugins/platforms/onebot/semantic_judge.py"),
    ("hermes/core/plugins/platforms/onebot/group_state.py", "hermes/core/plugins/platforms/onebot/group_state.py"),
    ("hermes/core/plugins/platforms/onebot/group_executor.py", "hermes/core/plugins/platforms/onebot/group_executor.py"),
    ("hermes/core/plugins/platforms/onebot/trigger_coordinator.py", "hermes/core/plugins/platforms/onebot/trigger_coordinator.py"),
    ("hermes/core/plugins/platforms/onebot/media_pipeline.py", "hermes/core/plugins/platforms/onebot/media_pipeline.py"),
    # Hermes plugins — Knowledge Base
    ("hermes/core/plugins/knowledge-base/__init__.py", "hermes/core/plugins/knowledge-base/__init__.py"),
    ("hermes/core/plugins/knowledge-base/knowledge_base_tool.py", "hermes/core/plugins/knowledge-base/knowledge_base_tool.py"),
    # Hermes memory system (v0.11.0 EPI layer)
    ("hermes/core/agent/memory/gateway.py", "hermes/core/agent/memory/gateway.py"),
    ("hermes/core/agent/memory/episodic_index.py", "hermes/core/agent/memory/episodic_index.py"),
    ("hermes/core/agent/memory/short_term.py", "hermes/core/agent/memory/short_term.py"),
    ("hermes/core/agent/memory/retrieval.py", "hermes/core/agent/memory/retrieval.py"),
    ("hermes/core/agent/memory/obsidian.py", "hermes/core/agent/memory/obsidian.py"),
    ("hermes/core/agent/memory/store.py", "hermes/core/agent/memory/store.py"),
    ("hermes/core/agent/model_metadata.py", "hermes/core/agent/model_metadata.py"),
    ("hermes/core/tools/memory_gateway_tool.py", "hermes/core/tools/memory_gateway_tool.py"),
    ("hermes/core/tools/chat_history_search_tool.py", "hermes/core/tools/chat_history_search_tool.py"),
    ("hermes/core/corpus_history.py", "hermes/core/corpus_history.py"),
    ("hermes/core/tools/browser_tool.py", "hermes/core/tools/browser_tool.py"),
    ("hermes/core/tools/vision_tools.py", "hermes/core/tools/vision_tools.py"),
    ("hermes/core/tools/web_tools.py", "hermes/core/tools/web_tools.py"),
    ("hermes/core/hermes_cli/hooks.py", "hermes/core/hermes_cli/hooks.py"),
    ("hermes/core/agent/auxiliary_client.py", "hermes/core/agent/auxiliary_client.py"),
    ("hermes/core/requirements.txt", "hermes/core/requirements.txt"),
    # Hermes scripts
    ("hermes/core/scripts/qq-db-recover.py", "hermes/core/scripts/qq-db-recover.py"),
    ("hermes/core/scripts/extract_qq_chat.py", "hermes/core/scripts/extract_qq_chat.py"),
    ("hermes/core/scripts/qq_chat_restore.py", "hermes/core/scripts/qq_chat_restore.py"),
    ("hermes/core/scripts/bandori_sync.py", "hermes/core/scripts/bandori_sync.py"),
    # Dashboard
    ("modules/dashboard/server.py", "modules/dashboard/server.py"),
    ("modules/dashboard/static/index.html", "modules/dashboard/static/index.html"),
    # Scripts
    ("extras/scripts/install.py", "extras/scripts/install.py"),
    ("extras/scripts/setup_config.py", "extras/scripts/setup_config.py"),
    ("extras/scripts/decrypt_cvpkg.py", "extras/scripts/decrypt_cvpkg.py"),
    ("extras/scripts/qzone-post.py", "extras/scripts/qzone-post.py"),
    # Templates
    ("templates/config-template.yaml", "templates/config-template.yaml"),
    ("templates/SOUL-template.md", "templates/SOUL-template.md"),
    ("templates/.env.template", "templates/.env.template"),
    ("templates/napcat/onebot11.json", "templates/napcat/onebot11.json"),
    ("templates/napcat/napcat.json", "templates/napcat/napcat.json"),
    # Bat files
    ("install.bat", "install.bat"),
    ("PeiZhiAPI.bat", "PeiZhiAPI.bat"),
    ("start.bat", "start.bat"),
    ("Stop-All.bat", "Stop-All.bat"),
    # NapCat
    ("napcat/napcat/node_modules", "napcat/napcat/node_modules"),
    # TTS & Live2D
    ("modules/tts/ts_adapter_template.py", "modules/tts/ts_adapter_template.py"),
    ("modules/live2d/main.js", "modules/live2d/main.js"),
    ("modules/live2d/preload.js", "modules/live2d/preload.js"),
    ("modules/live2d/conf.json", "modules/live2d/conf.json"),
    ("modules/live2d/hermes-ws.js", "modules/live2d/hermes-ws.js"),
    ("modules/live2d/download-backend.js", "modules/live2d/download-backend.js"),
    ("modules/live2d/renderer/app.js", "modules/live2d/renderer/app.js"),
    ("modules/live2d/renderer/index.html", "modules/live2d/renderer/index.html"),
    # Docs
    ("CHANGELOG.md", "CHANGELOG.md"),
    ("README.md", "README.md"),
    ("VERSION", "VERSION"),
]

# Files to NEVER overwrite (user configs)
PRESERVE = [
    "config.yaml",
    "SOUL.md",
    ".env",
]

def upgrade(source_root: str = None):
    if source_root:
        src = Path(source_root)
    else:
        src = BOT_DIR

    updated = []
    skipped = []
    for src_rel, dst_rel in UPGRADE_MAP:
        src_path = src / src_rel
        if not src_path.exists():
            skipped.append(f"(missing) {src_rel}")
            continue

        # 目标1: HERMES_HOME（实际运行目录）— strip 'hermes/' 前缀
        home_rel = dst_rel
        for prefix in ("hermes/core/", "hermes/"):
            if home_rel.startswith(prefix):
                home_rel = home_rel[len(prefix):]
        if home_rel.startswith("modules/"):
            home_rel = home_rel  # modules 目标在 BOT_DIR，见下
        dst_home = HERMES_HOME / home_rel
        try:
            dst_home.parent.mkdir(parents=True, exist_ok=True)
            if src_path.is_dir():
                if dst_home.exists():
                    shutil.rmtree(dst_home)
                shutil.copytree(src_path, dst_home)
            else:
                shutil.copy2(src_path, dst_home)
            updated.append(f"{dst_rel} -> ~/.hermes/{home_rel}")
        except Exception as e:
            skipped.append(f"(error) {dst_rel}: {e}")

        # 目标2: BOT_DIR（模板目录保留，供下次 upgrade 用）
        dst_tpl = BOT_DIR / dst_rel
        try:
            dst_tpl.parent.mkdir(parents=True, exist_ok=True)
            if src_path.is_dir():
                if dst_tpl.exists():
                    shutil.rmtree(dst_tpl)
                shutil.copytree(src_path, dst_tpl)
            else:
                shutil.copy2(src_path, dst_tpl)
        except Exception:
            pass

    print(f"Upgrade complete: {len(updated)} files updated, {len(skipped)} skipped")
    print()
    print("Preserved user configs:")
    for p in PRESERVE:
        home_cfg = HERMES_HOME / p
        if home_cfg.exists():
            print(f"  {home_cfg} (untouched)")
    print()
    print("Run PeiZhiAPI.bat if you changed LLM provider.")
    return updated


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else None
    upgrade(src)
