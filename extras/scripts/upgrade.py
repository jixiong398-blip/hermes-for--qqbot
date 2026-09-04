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
    ("hermes/core/gateway/config.py", "hermes/core/gateway/config.py"),
    ("hermes/core/gateway/session.py", "hermes/core/gateway/session.py"),
    ("hermes/core/gateway/platforms/base.py", "hermes/core/gateway/platforms/base.py"),
    ("hermes/core/gateway/delivery_ledger.py", "hermes/core/gateway/delivery_ledger.py"),
    ("hermes/core/gateway/session_stall.py", "hermes/core/gateway/session_stall.py"),
    ("hermes/core/gateway/shutdown_flush.py", "hermes/core/gateway/shutdown_flush.py"),
    ("hermes/core/gateway/turn_lease.py", "hermes/core/gateway/turn_lease.py"),
    # Agent runtime — required by Gateway contract retries and persistence gates
    ("hermes/core/run_agent.py", "hermes/core/run_agent.py"),
    ("hermes/core/agent/empty_response_guard.py", "hermes/core/agent/empty_response_guard.py"),
    ("hermes/core/agent/error_surface.py", "hermes/core/agent/error_surface.py"),
    ("hermes/core/agent/errors.py", "hermes/core/agent/errors.py"),
    ("hermes/core/agent/repetition_guard.py", "hermes/core/agent/repetition_guard.py"),
    ("hermes/core/agent/session_activity.py", "hermes/core/agent/session_activity.py"),
    ("hermes/core/agent/message_sanitization.py", "hermes/core/agent/message_sanitization.py"),
    ("hermes/core/agent/provider_projection.py", "hermes/core/agent/provider_projection.py"),
    ("hermes/core/agent/turn_context_contract.py", "hermes/core/agent/turn_context_contract.py"),
    ("hermes/core/agent/message_metadata_contract.py", "hermes/core/agent/message_metadata_contract.py"),
    # Hermes plugins — OneBot (v0.10.5+ three-phase pipeline)
    ("hermes/core/plugins/platforms/onebot/adapter.py", "hermes/core/plugins/platforms/onebot/adapter.py"),
    # OneBot compatibility/auth modules imported by the adapter
    ("hermes/core/plugins/platforms/onebot/config_discovery.py", "hermes/core/plugins/platforms/onebot/config_discovery.py"),
    ("hermes/core/plugins/platforms/onebot/contract.py", "hermes/core/plugins/platforms/onebot/contract.py"),
    ("hermes/core/plugins/platforms/onebot/transport_contract.py", "hermes/core/plugins/platforms/onebot/transport_contract.py"),
    ("hermes/core/plugins/platforms/onebot/plugin.yaml", "hermes/core/plugins/platforms/onebot/plugin.yaml"),
    ("hermes/core/plugins/platforms/onebot/semantic_judge.py", "hermes/core/plugins/platforms/onebot/semantic_judge.py"),
    ("hermes/core/plugins/platforms/onebot/group_state.py", "hermes/core/plugins/platforms/onebot/group_state.py"),
    ("hermes/core/plugins/platforms/onebot/group_executor.py", "hermes/core/plugins/platforms/onebot/group_executor.py"),
    ("hermes/core/plugins/platforms/onebot/trigger_coordinator.py", "hermes/core/plugins/platforms/onebot/trigger_coordinator.py"),
    ("hermes/core/plugins/platforms/onebot/media_pipeline.py", "hermes/core/plugins/platforms/onebot/media_pipeline.py"),
    # Hermes plugins — Knowledge Base
    ("hermes/core/plugins/knowledge-base/__init__.py", "hermes/core/plugins/knowledge-base/__init__.py"),
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
    # SessionDB compatibility ports and read-only replay tooling
    ("hermes/core/hermes_state.py", "hermes/core/hermes_state.py"),
    ("hermes/core/hermes_state_common.py", "hermes/core/hermes_state_common.py"),
    ("hermes/core/hermes_state_common_compat.py", "hermes/core/hermes_state_common_compat.py"),
    ("hermes/core/hermes_state_portability.py", "hermes/core/hermes_state_portability.py"),
    ("hermes/core/hermes_state_portability_compat.py", "hermes/core/hermes_state_portability_compat.py"),
    ("hermes/core/hermes_state_replay.py", "hermes/core/hermes_state_replay.py"),
    ("hermes/core/hermes_state_schema.py", "hermes/core/hermes_state_schema.py"),
    ("hermes/core/hermes_state_schema_probe.py", "hermes/core/hermes_state_schema_probe.py"),
    ("hermes/core/hermes_state_search.py", "hermes/core/hermes_state_search.py"),
    ("hermes/core/hermes_state_v26_compat.py", "hermes/core/hermes_state_v26_compat.py"),
    ("hermes/core/scripts/sessiondb_replay.py", "hermes/core/scripts/sessiondb_replay.py"),
    # Environment safety helper imported by active backends
    ("hermes/core/tools/environments/contract.py", "hermes/core/tools/environments/contract.py"),
    ("hermes/core/tools/spill_safety.py", "hermes/core/tools/spill_safety.py"),
    ("hermes/core/requirements.txt", "hermes/core/requirements.txt"),
    # Hermes scripts
    ("hermes/core/scripts/qq-db-recover.py", "hermes/core/scripts/qq-db-recover.py"),
    ("hermes/core/scripts/bandori_sync.py", "hermes/core/scripts/bandori_sync.py"),
    # Dashboard
    ("modules/dashboard/server.py", "modules/dashboard/server.py"),
    ("modules/dashboard/static/index.html", "modules/dashboard/static/index.html"),
    # Scripts
    ("extras/scripts/install.py", "extras/scripts/install.py"),
    ("extras/scripts/setup_config.py", "extras/scripts/setup_config.py"),
    ("extras/scripts/audit_upgrade_map.py", "extras/scripts/audit_upgrade_map.py"),
    ("extras/scripts/decrypt_cvpkg.py", "extras/scripts/decrypt_cvpkg.py"),
    ("extras/scripts/qzone-post.py", "extras/scripts/qzone-post.py"),
    # Templates
    ("templates/config-template.yaml", "templates/config-template.yaml"),
    ("templates/SOUL-template.md", "templates/SOUL-template.md"),
    ("templates/.env.template", "templates/.env.template"),
    # Bat files
    ("install.bat", "install.bat"),
    ("配置API.bat", "配置API.bat"),
    ("start.bat", "start.bat"),
    ("Stop-All.bat", "Stop-All.bat"),
    # NapCat
    # TTS & Live2D
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

# Runtime Python is a package graph rather than a stable hand-maintained file
# list.  Keep the explicit map for non-Python assets and high-risk boundaries,
# then close the local ``hermes/core`` Python graph during each upgrade.  Tests,
# docs, VCS metadata, and hidden directories are never copied by this fallback.
MAX_DYNAMIC_CORE_FILES = 10_000
_DYNAMIC_CORE_EXCLUDED_DIRS = frozenset({"tests", "docs", ".git", "__pycache__"})


def _iter_dynamic_core_entries(source_root: Path):
    """Yield bounded Python source entries not already in ``UPGRADE_MAP``."""

    core_root = source_root / "hermes" / "core"
    if not core_root.is_dir():
        return
    existing = {src_rel.replace("\\", "/") for src_rel, _ in UPGRADE_MAP}
    count = 0
    for path in core_root.rglob("*.py"):
        if count >= MAX_DYNAMIC_CORE_FILES:
            break
        if path.is_symlink() or not path.is_file():
            continue
        relative = path.relative_to(source_root).as_posix()
        parts = Path(relative).parts
        if (
            any(part in _DYNAMIC_CORE_EXCLUDED_DIRS for part in parts)
            or any(part.startswith(".") for part in parts)
            or relative in existing
        ):
            continue
        count += 1
        yield relative, relative


def _upgrade_entries(source_root: Path):
    """Return explicit entries plus the bounded runtime Python closure."""

    seen: set[tuple[str, str]] = set()
    for entry in UPGRADE_MAP:
        normalized = (entry[0].replace("\\", "/"), entry[1].replace("\\", "/"))
        if normalized not in seen:
            seen.add(normalized)
            yield entry
    for entry in _iter_dynamic_core_entries(source_root) or ():
        if entry not in seen:
            seen.add(entry)
            yield entry


def _is_link_or_junction(path: Path) -> bool:
    """Return true for symlinks and Windows junctions without following them."""

    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction()) if callable(is_junction) else False
    except OSError:
        return True


def _safe_relative_path(value: str) -> bool:
    """Accept only ordinary relative paths for source and destination entries."""

    try:
        path = Path(value)
    except (TypeError, ValueError):
        return False
    return bool(value) and not path.is_absolute() and ".." not in path.parts


def _inside(root: Path, candidate: Path) -> bool:
    """Check containment after resolving existing parent links."""

    try:
        root_value = root.resolve(strict=False)
        candidate_value = candidate.resolve(strict=False)
        return os.path.commonpath((str(root_value), str(candidate_value))) == str(root_value)
    except (OSError, ValueError):
        return False


def _validate_source_path(source_root: Path, relative: str) -> Path:
    """Validate an allowlisted source without following source links."""

    if not _safe_relative_path(relative):
        raise ValueError("upgrade source path must be relative and contained")
    candidate = source_root / relative
    if _is_link_or_junction(candidate):
        raise ValueError("upgrade source symlink/junction is not accepted")
    if not candidate.exists() or not _inside(source_root, candidate):
        raise ValueError("upgrade source path is outside the source root")
    return candidate


def _validate_destination_path(root: Path, relative: str) -> Path:
    """Validate a destination before mkdir/copy so links cannot escape root."""

    if not _safe_relative_path(relative):
        raise ValueError("upgrade destination path must be relative and contained")
    candidate = root / relative
    if _is_link_or_junction(candidate) or _is_link_or_junction(candidate.parent):
        raise ValueError("upgrade destination symlink/junction is not accepted")
    if not _inside(root, candidate):
        raise ValueError("upgrade destination path is outside the destination root")
    return candidate

def upgrade(source_root: str = None, *, dry_run: bool = False):
    if source_root:
        src = Path(source_root)
    else:
        src = BOT_DIR

    updated = []
    skipped = []
    for src_rel, dst_rel in _upgrade_entries(src):
        try:
            src_path = _validate_source_path(src, src_rel)
        except ValueError as error:
            if not (src / src_rel).exists():
                skipped.append(f"(missing) {src_rel}")
            else:
                skipped.append(f"(unsafe) {src_rel}: {error}")
            continue

        # 目标1: HERMES_HOME（实际运行目录）— strip 'hermes/' 前缀
        home_rel = dst_rel
        for prefix in ("hermes/core/", "hermes/"):
            if home_rel.startswith(prefix):
                home_rel = home_rel[len(prefix):]
        if home_rel.startswith("modules/"):
            home_rel = home_rel  # modules 目标在 BOT_DIR，见下
        try:
            dst_home = _validate_destination_path(HERMES_HOME, home_rel)
            if not dry_run:
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
        try:
            dst_tpl = _validate_destination_path(BOT_DIR, dst_rel)
            if not dry_run:
                dst_tpl.parent.mkdir(parents=True, exist_ok=True)
                if src_path.is_dir():
                    if dst_tpl.exists():
                        shutil.rmtree(dst_tpl)
                    shutil.copytree(src_path, dst_tpl)
                else:
                    shutil.copy2(src_path, dst_tpl)
        except Exception:
            pass

    label = "Upgrade dry-run" if dry_run else "Upgrade complete"
    verb = "planned" if dry_run else "updated"
    print(f"{label}: {len(updated)} files {verb}, {len(skipped)} skipped")
    print()
    print("Preserved user configs:")
    for p in PRESERVE:
        home_cfg = HERMES_HOME / p
        if home_cfg.exists():
            print(f"  {home_cfg} (untouched)")
    print()
    print("Run the API configuration batch file if you changed the LLM provider.")
    return updated


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv[1:]
    positional = [arg for arg in sys.argv[1:] if arg != "--dry-run"]
    src = positional[0] if positional else None
    upgrade(src, dry_run=dry_run)
