# QZone (QQ空间) Posting �?Session Details

## Script Source

**Path**: `/home/{{USERNAME}}/.local/bin/qzone-post`
**Tested**: 2026-05-13, successful post

```python
#!/usr/bin/env python3
"""Post QQ Space 说说 and log to memory."""
import sys, json, time, requests, sqlite3
from pathlib import Path

ONEBOT_HTTP = "http://127.0.0.1:3000"
TOKEN = "{{ONEBOT_TOKEN}}"
DB = Path.home() / ".hermes" / "memory_store.db"

def get_cookies():
    r = requests.post(f"{ONEBOT_HTTP}/get_cookies",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"domain": "qzone.qq.com"})
    data = r.json()
    if data.get("retcode") != 0 or not data.get("data"):
        raise RuntimeError("Failed to get QZone cookies")
    cookies = {}
    for item in data["data"]["cookies"].split(";"):
        if "=" in (item := item.strip()):
            k, v = item.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies

def get_gtk(skey):
    h = 5381
    for c in skey: h += (h << 5) + ord(c)
    return h & 0x7fffffff

def post_mood(content):
    cookies = get_cookies()
    uin = cookies.get("uin", "").replace("o", "")
    gtk = get_gtk(cookies.get("skey", ""))
    cookie_str = "; ".join(f"{k}={v}" for k,v in cookies.items())
    
    url = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"
    r = requests.post(url,
        params={"g_tk": gtk, "qzreferrer": f"https://user.qzone.qq.com/{uin}"},
        data={"con": content, "feedversion": 1, "ver": 1, "hostuin": uin,
              "format": "json", "code_version": 1},
        headers={"Cookie": cookie_str, "User-Agent": "Mozilla/5.0"})
    result = r.json()
    if result.get("code") == 0:
        db = sqlite3.connect(str(DB))
        db.execute("INSERT INTO long_term_entries (category, key, value, tags, confidence, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                   ("qzone", f"post_{int(time.time())}", content, "[]", 1.0, time.time(), time.time()))
        db.commit()
        db.close()
    return result

if __name__ == "__main__":
    content = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "今天天气真好�?
    result = post_mood(content)
    if result.get("code") == 0:
        print(f"OK: {content[:60]}...")
    else:
        print(f"FAIL: {result.get('message', 'unknown')}")
```

## Test Result (2026-05-13)
- Command: `qzone-post "今天的天气真好，好久没更新空间了，大家最近过得怎么样呀�?`
- Response: `OK: 今天的天气真好，好久没更新空间了，大家最近过得怎么样呀�?..`
- API latency: ~2s (cookie fetch + g_tk calc + QZone POST)
- Memory store DB: table `long_term_entries` with `category='qzone'` was auto-created on first successful write

## Performance Notes
- Cookie fetch via OneBot HTTP is sub-100ms (local loopback)
- g_tk calculation is near-instant
- QZone API response time is ~1-2s (Tencent's public API)
- Total round-trip: ~2-3s for a short text post

## Integration Ideas
- **Daily auto-post**: Use Hermes cron to run `qzone-post "内容"` at a fixed time daily
- **Agent-triggered**: Have the bot decide content via LLM and call qzone-post programmatically
- **Multi-line posts**: The script joins all args with spaces �?use quotes for multi-sentence content
