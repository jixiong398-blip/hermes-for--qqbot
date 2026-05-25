#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QQBot Installer -- create HERMES_HOME, deploy config, pre-configure NapCat"""
import os, json, secrets, shutil
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
HERMES_HOME = Path.home() / ".hermes"
NAPCAT_CFG = BOT_DIR / "napcat" / "napcat" / "config"


def setup_napcat_defaults():
    """Pre-configure NapCat anti-detection + generate token for later use."""
    token = secrets.token_urlsafe(16)
    NAPCAT_CFG.mkdir(parents=True, exist_ok=True)

    # napcat global defaults -- anti-detection ON (NapCat reads this as base)
    napcat = NAPCAT_CFG / "napcat.json"
    cfg = {"fileLog": False, "consoleLog": True, "fileLogLevel": "debug", "consoleLogLevel": "info",
           "packetBackend": "auto", "packetServer": "", "o3HookMode": 1,
           "bypass": {"hook": True, "window": True, "module": True, "process": True, "container": True, "js": True},
           "autoTimeSync": True}
    json.dump(cfg, napcat.open("w", encoding="utf-8"), indent=2, ensure_ascii=False)
    print(f"  NapCat anti-detection: ON")

    return token


def setup():
    token = setup_napcat_defaults()

    HERMES_HOME.mkdir(parents=True, exist_ok=True)
    print(f"HERMES_HOME: {HERMES_HOME}")

    # Write .env
    env_file = BOT_DIR / ".env"
    if not env_file.exists():
        with open(env_file, "w", encoding="utf-8") as f:
            f.write(f"# QQBot Environment\n")
            f.write(f"HERMES_HOME={HERMES_HOME}\n")
            f.write(f"ONEBOT_WS_URL=ws://127.0.0.1:3001/\n")
            f.write(f"ONEBOT_HTTP_URL=http://127.0.0.1:3000\n")
            f.write(f"ONEBOT_ACCESS_TOKEN={token}\n")
            f.write("OPENAI_API_KEY=sk-your-key-here\n")
        print(f"  .env written")

    # Copy templates
    tpl_dir = BOT_DIR / "templates"
    for fname in ("config-template.yaml", "SOUL-template.md"):
        src = tpl_dir / fname
        dst_name = fname.replace("-template", "")
        dst = HERMES_HOME / dst_name
        if src.exists() and not dst.exists():
            shutil.copy(src, dst)
            print(f"  Created {dst_name}")

    print()
    print("Done!")
    print()
    print("  [IMPORTANT] After NapCat login:")
    print("    1. Run PeiZhiAPI.bat")
    print("    2. Enter your QQ number (shown in NapCat window)")
    print("    -> Enables WS :3001 and HTTP :3000 ports")
    print("    -> Also configures your LLM provider")
    print()


if __name__ == "__main__":
    setup()
