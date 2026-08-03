#!/usr/bin/env python3
"""Export QQ group chat history via OneBot v11 API -> JSON files."""
import json, os, sys, time
from pathlib import Path
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import URLError

BASE = os.getenv("NAPCAT_HTTP_URL", "http://127.0.0.1:3000")
TOKEN = os.getenv("ONEBOT_ACCESS_TOKEN", "")
if not TOKEN:
    print("Error: ONEBOT_ACCESS_TOKEN not set in .env", file=sys.stderr); sys.exit(1)
HDR = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
OUTDIR = os.getenv("QQ_EXPORT_DIR", str(Path(os.getenv("HERMES_HOME", os.path.expanduser("~/.hermes"))) / "exports"))

def api(action: str, **params) -> dict:
    req = Request(f"{BASE}/{action}", data=json.dumps(params).encode(), headers=HDR)
    try:
        with urlopen(req, timeout=30) as r:
            d = json.loads(r.read())
            return d.get("data", {}) if d.get("retcode") == 0 else {}
    except URLError as e:
        print(f"  API error: {e}")
        return {}

def export_group(group_id: int, group_name: str):
    path = f"{OUTDIR}/group_{group_id}"
    import os; os.makedirs(path, exist_ok=True)

    all_msgs = []
    seq = 0
    page = 0
    while True:
        page += 1
        data = api("get_group_msg_history", group_id=group_id, message_seq=seq, count=500)
        msgs = data.get("messages", [])
        if not msgs:
            break
        all_msgs.extend(msgs)
        oldest = min(m.get("message_seq", 0) for m in msgs)
        seq = oldest
        ts = datetime.fromtimestamp(msgs[-1]["time"]).strftime("%m-%d %H:%M") if msgs else "?"
        print(f"  Page {page}: +{len(msgs)} msgs, oldest={ts}, seq→{seq}")
        if len(msgs) < 500:
            break

    all_msgs.sort(key=lambda m: m.get("time", 0))
    outfile = f"{path}/full.json"
    with open(outfile, "w", encoding="utf-8") as f:
        json.dump(all_msgs, f, ensure_ascii=False, indent=2)

    t0 = datetime.fromtimestamp(all_msgs[0]["time"]).strftime("%Y-%m-%d %H:%M") if all_msgs else "?"
    t1 = datetime.fromtimestamp(all_msgs[-1]["time"]).strftime("%Y-%m-%d %H:%M") if all_msgs else "?"
    print(f"  ✓ {group_name}: {len(all_msgs)} messages, {t0} ~ {t1} → {outfile}")
    return len(all_msgs)

def main():
    groups = api("get_group_list")
    if not groups:
        print("Failed to get group list")
        return

    print(f"Found {len(groups)} groups\n")
    total = 0
    for g in groups:
        gid = g["group_id"]
        gname = g["group_name"]
        print(f"--- {gid} ({gname}) ---")
        n = export_group(gid, gname)
        total += n

    print(f"\nDONE. {total} messages exported to {OUTDIR}/")

if __name__ == "__main__":
    main()
