#!/usr/bin/env python3
"""FixNapCat -- enable WS/HTTP ports after QQ login.

Run AFTER: NapCat started, QR scanned, login complete.
Reads auto-generated config, extracts QQ + token,
generates onebot11_<QQ>.json with WS :3001 + HTTP :3000.
"""
import json, os, re, shutil
from pathlib import Path

BOT_DIR = Path(__file__).resolve().parent.parent
CFG = BOT_DIR / "napcat" / "napcat" / "config"

def find_qq():
    for p in ["onebot11_*.json", "napcat_*.json"]:
        for f in sorted(CFG.glob(p)):
            m = re.match(rf'{p.replace("*","(\\d+)")}', f.name)
            if m: return m.group(1)
    return None

def main():
    qq = find_qq()
    if not qq:
        print("no NapCat config found - login first")
        return
    print(f"QQ: {qq}")

    # extract token from auto-generated config
    old = CFG / f"onebot11_{qq}.json"
    token = ""
    if old.exists():
        try:
            d = json.loads(old.read_text(encoding="utf-8"))
            for s in d.get("network",{}).get("httpServers",[]):
                if s.get("token"): token = s["token"]; break
            if not token:
                for s in d.get("network",{}).get("websocketServers",[]):
                    if s.get("token"): token = s["token"]; break
        except: pass

    if not token:
        import secrets; token = secrets.token_urlsafe(16)
        print("generated new token")

    # generate onebot11 config
    tpl = BOT_DIR / "templates" / "napcat" / "onebot11.json"
    if tpl.exists():
        cfg = tpl.read_text(encoding="utf-8").replace("{{ONEBOT_TOKEN}}", token)
        (CFG / f"onebot11_{qq}.json").write_text(cfg, encoding="utf-8")
        print(f"onebot11_{qq}.json: WS :3001 + HTTP :3000")

    # copy anti-detection config
    tpl2 = BOT_DIR / "templates" / "napcat" / "napcat.json"
    if tpl2.exists():
        shutil.copy(tpl2, CFG / f"napcat_{qq}.json")
        print(f"napcat_{qq}.json: anti-detection ON")

    print()
    print("done - restart NapCat to apply")

if __name__ == "__main__":
    main()