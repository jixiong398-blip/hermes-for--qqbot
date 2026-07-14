#!/usr/bin/env python3
"""Post QQ Space 说说 and log to memory — with lock, dedup, and robust error handling."""
import sys, json, time, requests, sqlite3, os, yaml
from pathlib import Path

ONEBOT_HTTP = "http://127.0.0.1:3000"

def _load_token():
    """Read OneBot access_token from .env or config.yaml."""
    # 1. env
    tok = os.getenv("ONEBOT_ACCESS_TOKEN", "")
    if tok: return tok
    # 2. config.yaml
    cfg = Path.home() / ".hermes" / "config.yaml"
    if cfg.exists():
        try:
            data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
            tok = data.get("platforms", {}).get("onebot", {}).get("extra", {}).get("access_token", "")
            if tok: return tok
        except Exception:
            pass
    # 3. NapCat onebot11 config
    import glob as _glob
    for f in sorted(_glob.glob(str(Path("napcat/napcat/config/onebot11_*.json"))), reverse=True):
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
            for srv in d.get("network", {}).get("httpServers", []):
                t = srv.get("token", "").strip()
                if t: return t
        except Exception:
            continue
    return ""

TOKEN = _load_token()
DB = Path.home() / ".hermes" / "memory_store.db"

def get_cookies():
    r = requests.post(f"{ONEBOT_HTTP}/get_cookies",
        headers={"Authorization": f"Bearer {TOKEN}"},
        json={"domain": "qzone.qq.com"},
        timeout=10)
    data = r.json()
    if data.get("retcode") != 0 or not data.get("data"):
        raise RuntimeError("Failed to get QZone cookies: retcode={}, data={!r}".format(
            data.get("retcode"), data.get("data")))
    cookies = {}
    for item in data["data"]["cookies"].split(";"):
        if "=" in (item := item.strip()):
            k, v = item.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies

def get_gtk(skey):
    h = 5381
    for c in skey:
        h += (h << 5) + ord(c)
    return h & 0x7fffffff

def normalize(s):
    """Strip whitespace, punctuation, emoji, brackets — compare semantic content only."""
    import re
    s = re.sub(r'[\s\u3000\xa0]+', '', s)
    s = re.sub(r'[，。！？、…~～\[\]【】()（）{}「」『』""\'\'#*…—]', '', s)
    s = re.sub(r'[\U0001f000-\U0001ffff]', '', s)
    return s[:60]

def is_duplicate(content):
    """Check if the same content was posted in last 24h.

    Matches on normalized content: LLM may produce slightly different
    punctuation/whitespace for the same semantic idea, so strip those
    before comparison.
    """
    norm_new = normalize(content)
    if len(norm_new) < 10:
        return False
    try:
        db = sqlite3.connect(str(DB), timeout=5)
        cutoff = time.time() - 86400
        rows = db.execute(
            "SELECT value FROM long_term_entries WHERE category='qzone' AND created_at > ? ORDER BY created_at DESC",
            (cutoff,)
        ).fetchall()
        db.close()
        for (value,) in rows:
            if normalize(value) == norm_new:
                return True
            if norm_new in normalize(value) or normalize(value) in norm_new:
                return True
            words_new = set(re.findall(r'[\u4e00-\u9fff]{2,}', content))
            words_old = set(re.findall(r'[\u4e00-\u9fff]{2,}', value or ''))
            if words_new and words_old:
                overlap = len(words_new & words_old) / min(len(words_new), len(words_old))
                if overlap > 0.8:
                    return True
        return False
    except Exception:
        return False

def write_to_db(content):
    """Write to LTM with retry on lock."""
    for attempt in range(3):
        try:
            db = sqlite3.connect(str(DB), timeout=10)
            db.execute(
                "INSERT INTO long_term_entries (category, key, value, tags, confidence, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                ("qzone", f"post_{int(time.time())}", content, "[]", 1.0, time.time(), time.time()),
            )
            db.commit()
            db.close()
            return True
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < 2:
                time.sleep(1 + attempt * 2)
            else:
                raise
    return False

def post_mood(content):
    """Post to QZone. Returns dict with 'code' key (0 = success)."""
    cookies = get_cookies()
    uin = cookies.get("uin", "").replace("o", "")
    gtk = get_gtk(cookies.get("skey", ""))
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())

    url = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6"
    r = requests.post(
        url,
        params={"g_tk": gtk, "qzreferrer": f"https://user.qzone.qq.com/{uin}"},
        data={
            "con": content,
            "feedversion": 1,
            "ver": 1,
            "hostuin": uin,
            "format": "json",
            "code_version": 1,
        },
        headers={"Cookie": cookie_str, "User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    try:
        result = r.json()
    except json.JSONDecodeError:
        # Response wasn't JSON — post might still have gone through.
        # Check HTTP status: 2xx means QZone accepted it but returned non-JSON.
        if 200 <= r.status_code < 300:
            result = {"code": 0, "message": "response_not_json_but_http_ok"}
        else:
            raise RuntimeError(f"QZone returned HTTP {r.status_code}: {r.text[:200]}")

    if result.get("code") == 0:
        write_to_db(content)
    return result

if __name__ == "__main__":
    content = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "今天天气真好～"

    # Check for duplicate before posting
    if is_duplicate(content):
        print(f"SKIP: duplicate content — already posted in last 24h")
        sys.exit(0)

    try:
        result = post_mood(content)
        if result.get("code") == 0:
            print(f"OK: {content[:60]}...")
        else:
            msg = result.get("message", "unknown")
            print(f"FAIL: {msg}")
            sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}")
        sys.exit(1)
