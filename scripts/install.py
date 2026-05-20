#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QQBot Installer — auto-generate token and write config"""
import os, secrets, shutil

BOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def setup():
    token = secrets.token_urlsafe(16)

    # Write .env in bot-template root
    env = os.path.join(BOT_DIR, ".env")
    if not os.path.exists(env):
        with open(env, "w", encoding="utf-8") as f:
            f.write("# QQBot Environment — edit OPENAI_API_KEY below\n")
            f.write(f"ONEBOT_WS_URL=ws://127.0.0.1:3001/\n")
            f.write(f"ONEBOT_HTTP_URL=http://127.0.0.1:3000\n")
            f.write(f"ONEBOT_ACCESS_TOKEN={token}\n")
            f.write(f"HERMES_HOME={BOT_DIR}\n")
            f.write("OPENAI_API_KEY=sk-your-key-here\n")
        print(f"Token: {token}")
        print(f"Config written to {env}")

    # Copy templates to root
    tpl = os.path.join(BOT_DIR, "templates")
    for fname in ("config-template.yaml", "SOUL-template.md"):
        src = os.path.join(tpl, fname)
        dst_name = fname.replace("-template", "")
        dst = os.path.join(BOT_DIR, dst_name)
        if os.path.exists(src) and not os.path.exists(dst):
            shutil.copy(src, dst)
            print(f"Created {dst_name} from template")

    print("Done. Edit config.yaml (API key) and SOUL.md, then run start.bat")

if __name__ == "__main__":
    setup()
