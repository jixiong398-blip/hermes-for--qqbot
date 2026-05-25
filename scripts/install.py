#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QQBot Installer -- create HERMES_HOME and deploy config templates"""
import os, shutil
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent  # bot-template root
HERMES_HOME = Path.home() / ".hermes"

def setup():
    # Create HERMES_HOME directory
    HERMES_HOME.mkdir(parents=True, exist_ok=True)
    print(f"HERMES_HOME: {HERMES_HOME}")

    # Copy templates â†' HERMES_HOME (user edits these)
    tpl_dir = BOT_DIR / "templates"
    for fname in ("config-template.yaml", "SOUL-template.md"):
        src = tpl_dir / fname
        dst_name = fname.replace("-template", "")
        dst = HERMES_HOME / dst_name
        if src.exists() and not dst.exists():
            shutil.copy(src, dst)
            print(f"  Created {dst_name} -> {dst}")

    print()
    print("Done! Base config files created.")

    # Create knowledge directory
    kb = HERMES_HOME / "knowledge"
    kb.mkdir(exist_ok=True)
    (kb / ".gitkeep").touch(exist_ok=True)
    print(f"  Knowledge dir: {kb}")

    print()
    print("Next: run the API config tool to set up your LLM provider.")

if __name__ == "__main__":
    setup()
