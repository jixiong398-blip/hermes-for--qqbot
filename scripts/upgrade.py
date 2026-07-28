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
BOT_DIR = SCRIPT_DIR.parent
HERMES_HOME = Path.home() / ".hermes"

# Files to upgrade (source -> destination relative to BOT_DIR)
UPGRADE_MAP = [
    # Hermes core — gateway
    ("hermes/gateway/run.py", "hermes/gateway/run.py"),
    ("hermes/gateway/platforms/onebot/adapter.py", "hermes/gateway/platforms/onebot/adapter.py"),
    # Hermes plugins — OneBot (v0.10.5+ three-phase pipeline)
    ("hermes/plugins/platforms/onebot/adapter.py", "hermes/plugins/platforms/onebot/adapter.py"),
    ("hermes/plugins/platforms/onebot/semantic_judge.py", "hermes/plugins/platforms/onebot/semantic_judge.py"),
    ("hermes/plugins/platforms/onebot/group_state.py", "hermes/plugins/platforms/onebot/group_state.py"),
    ("hermes/plugins/platforms/onebot/group_executor.py", "hermes/plugins/platforms/onebot/group_executor.py"),
    ("hermes/plugins/platforms/onebot/trigger_coordinator.py", "hermes/plugins/platforms/onebot/trigger_coordinator.py"),
    ("hermes/plugins/platforms/onebot/media_pipeline.py", "hermes/plugins/platforms/onebot/media_pipeline.py"),
    # Hermes plugins — Knowledge Base
    ("hermes/plugins/knowledge-base/__init__.py", "hermes/plugins/knowledge-base/__init__.py"),
    ("hermes/plugins/knowledge-base/knowledge_base_tool.py", "hermes/plugins/knowledge-base/knowledge_base_tool.py"),
    # Hermes memory system (v0.11.0 EPI layer)
    ("hermes/agent/memory/gateway.py", "hermes/agent/memory/gateway.py"),
    ("hermes/agent/memory/episodic_index.py", "hermes/agent/memory/episodic_index.py"),
    ("hermes/agent/memory/short_term.py", "hermes/agent/memory/short_term.py"),
    ("hermes/agent/memory/retrieval.py", "hermes/agent/memory/retrieval.py"),
    ("hermes/agent/memory/obsidian.py", "hermes/agent/memory/obsidian.py"),
    ("hermes/tools/memory_gateway_tool.py", "hermes/tools/memory_gateway_tool.py"),
    ("hermes/tools/chat_history_search_tool.py", "hermes/tools/chat_history_search_tool.py"),
    ("hermes/tools/browser_tool.py", "hermes/tools/browser_tool.py"),
    ("hermes/tools/vision_tools.py", "hermes/tools/vision_tools.py"),
    ("hermes/tools/web_tools.py", "hermes/tools/web_tools.py"),
    ("hermes/hermes_cli/hooks.py", "hermes/hermes_cli/hooks.py"),
    ("hermes/agent/auxiliary_client.py", "hermes/agent/auxiliary_client.py"),
    ("hermes/requirements.txt", "hermes/requirements.txt"),
    # Hermes scripts
    ("hermes/scripts/qq-db-recover.py", "hermes/scripts/qq-db-recover.py"),
    ("hermes/scripts/extract_qq_chat.py", "hermes/scripts/extract_qq_chat.py"),
    ("hermes/scripts/qq_chat_restore.py", "hermes/scripts/qq_chat_restore.py"),
    ("hermes/scripts/bandori_sync.py", "hermes/scripts/bandori_sync.py"),
    # Dashboard
    ("modules/dashboard/server.py", "modules/dashboard/server.py"),
    ("modules/dashboard/static/index.html", "modules/dashboard/static/index.html"),
    # Scripts
    ("scripts/install.py", "scripts/install.py"),
    ("scripts/setup_config.py", "scripts/setup_config.py"),
    ("scripts/decrypt_cvpkg.py", "scripts/decrypt_cvpkg.py"),
    ("scripts/qzone-post.py", "scripts/qzone-post.py"),
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
        dst_path = BOT_DIR / dst_rel
        if not src_path.exists():
            skipped.append(f"(missing) {src_rel}")
            continue
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        if src_path.is_dir():
            if dst_path.exists():
                shutil.rmtree(dst_path)
            shutil.copytree(src_path, dst_path)
        else:
            shutil.copy2(src_path, dst_path)
        updated.append(src_rel)

    print(f"Upgrade complete: {len(updated)} files updated, {len(skipped)} skipped")
    print()
    print("Preserved user configs:")
    for p in PRESERVE:
        cfg = BOT_DIR / p
        home_cfg = HERMES_HOME / p
        if home_cfg.exists():
            print(f"  {home_cfg} (untouched)")
    print()
    print("Run PeiZhiAPI.bat if you changed LLM provider.")
    return updated


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else None
    upgrade(src)
