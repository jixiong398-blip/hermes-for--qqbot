#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QQBot Installer — create HERMES_HOME and deploy config"""
import os, secrets, shutil
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent  # bot-template root
HERMES_HOME = Path.home() / ".hermes"

def setup():
    token = secrets.token_urlsafe(16)

    # Create HERMES_HOME directory
    HERMES_HOME.mkdir(parents=True, exist_ok=True)
    print(f"HERMES_HOME: {HERMES_HOME}")

    # Write .env in bot-template root (for start.bat to read)
    env_file = BOT_DIR / ".env"
    if not env_file.exists():
        with open(env_file, "w", encoding="utf-8") as f:
            f.write(f"# QQBot Environment\n")
            f.write(f"HERMES_HOME={HERMES_HOME}\n")
            f.write(f"ONEBOT_WS_URL=ws://127.0.0.1:3001/\n")
            f.write(f"ONEBOT_HTTP_URL=http://127.0.0.1:3000\n")
            f.write(f"ONEBOT_ACCESS_TOKEN={token}\n")
            f.write("OPENAI_API_KEY=sk-your-key-here\n")
        print(f"Token: {token}")
        print(f".env written to {env_file}")

    # Copy templates → HERMES_HOME
    tpl_dir = BOT_DIR / "templates"
    for fname in ("config-template.yaml", "SOUL-template.md"):
        src = tpl_dir / fname
        dst_name = fname.replace("-template", "")
        dst = HERMES_HOME / dst_name
        if src.exists() and not dst.exists():
            shutil.copy(src, dst)
            print(f"Created {dst_name} → {dst}")

    # Copy TOOLS.md if it came with the package
    tools_src = tpl_dir / "TOOLS-template.md"
    tools_dst = HERMES_HOME / "TOOLS.md"
    if tools_src.exists() and not tools_dst.exists():
        shutil.copy(tools_src, tools_dst)
        print("Created TOOLS.md")

    print()
    print("Done! Now edit:")
    print(f"  {HERMES_HOME / 'config.yaml'}  ← set your API key")
    print(f"  {HERMES_HOME / 'SOUL.md'}      ← write character persona")
    print("Then run start.bat")

if __name__ == "__main__":
    setup()
