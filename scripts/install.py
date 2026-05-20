#!/usr/bin/env python3
"""QQBot 安装器 — 自动生成 token + 写入配置"""
import os, secrets, shutil
HOME = os.path.expanduser("~")
BOT_DIR = os.path.join(HOME, "bot")
def setup():
    token = secrets.token_urlsafe(16)
    os.makedirs(BOT_DIR, exist_ok=True)
    env = os.path.join(BOT_DIR, ".env")
    if not os.path.exists(env):
        with open(env, "w") as f:
            f.write(f"ONEBOT_WS_URL=ws://127.0.0.1:3001/\n")
            f.write(f"ONEBOT_HTTP_URL=http://127.0.0.1:3000\n")
            f.write(f"ONEBOT_ACCESS_TOKEN={token}\n")
            f.write(f"HERMES_HOME={os.path.join(BOT_DIR, 'hermes')}\n")
            f.write("OPENAI_API_KEY={{YOUR_API_KEY}}\n")
        print(f"Token: {token}")
    src = os.path.join(BOT_DIR, "templates", "config-template.yaml")
    dst = os.path.join(BOT_DIR, "config.yaml")
    if os.path.exists(src) and not os.path.exists(dst):
        shutil.copy(src, dst)
    src = os.path.join(BOT_DIR, "templates", "SOUL-template.md")
    dst = os.path.join(BOT_DIR, "SOUL.md")
    if os.path.exists(src) and not os.path.exists(dst):
        shutil.copy(src, dst)
    print("Done. Edit SOUL.md and config.yaml, then start NapCat -> Hermes.")
if __name__ == "__main__":
    setup()
