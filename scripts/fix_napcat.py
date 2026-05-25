#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fix NapCat -- enable WS/HTTP ports using auto-generated config after login.

Run AFTER: NapCat started, QR scanned, login complete.
Reads NapCat's auto-generated config, extracts QQ number + token,
generates proper onebot11_<QQ>.json with WS :3001 + HTTP :3000.
"""
import json, os, re, glob as _glob
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
NAPCAT_CFG = BOT_DIR / "napcat" / "napcat" / "config"


def find_qq_from_config():
    """Find QQ number from auto-generated onebot11_*.json or napcat_*.json."""
    for pattern in ["onebot11_*.json", "napcat_*.json"]:
        for f in sorted(NAPCAT_CFG.glob(pattern)):
            # Extract QQ number from filename
            m = re.match(rf'{pattern.replace("*", "(\d+)")}', f.name)
            if m:
                return m.group(1)
    return None


def fix():
    qq = find_qq_from_config()
    if not qq:
        print("未找到 NapCat 配置。请先启动 NapCat 并扫码登录。")
        print(f"预期路径: {NAPCAT_CFG / 'onebot11_<QQ>.json'}")
        return

    print(f"找到 QQ: {qq}")

    # Read auto-generated config to extract token
    old_cfg = NAPCAT_CFG / f"onebot11_{qq}.json"
    token = ""
    if old_cfg.exists():
        try:
            data = json.loads(old_cfg.read_text(encoding="utf-8"))
            # Try to get existing token
            for server in data.get("network", {}).get("httpServers", []):
                if server.get("token"):
                    token = server["token"]
                    break
            if not token:
                for server in data.get("network", {}).get("websocketServers", []):
                    if server.get("token"):
                        token = server["token"]
                        break
        except Exception:
            pass

    if not token:
        print("警告: 未找到已有 token，将生成新 token")

    # Generate proper config
    tpl = BOT_DIR / "templates" / "napcat" / "onebot11.json"
    if not tpl.exists():
        print(f"模板不存在: {tpl}")
        return

    cfg_text = tpl.read_text(encoding="utf-8")
    cfg_text = cfg_text.replace("{{ONEBOT_TOKEN}}", token)

    dst = NAPCAT_CFG / f"onebot11_{qq}.json"
    dst.write_text(cfg_text, encoding="utf-8")
    print(f"已写入: {dst}")
    print(f"  WS:  ws://127.0.0.1:3001/")
    print(f"  HTTP: http://127.0.0.1:3000")
    print()

    # napcat anti-detection
    napcat_tpl = BOT_DIR / "templates" / "napcat" / "napcat.json"
    if napcat_tpl.exists():
        napcat_dst = NAPCAT_CFG / f"napcat_{qq}.json"
        shutil = __import__("shutil")
        shutil.copy(napcat_tpl, napcat_dst)
        print(f"已写入: {napcat_dst} (防检测 ON)")

    print()
    print("完成！重启 NapCat 后即可使用 WS/HTTP 端口。")


if __name__ == "__main__":
    fix()
