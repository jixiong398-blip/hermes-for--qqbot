# QQ Database Decrypt

## Purpose

Decrypt QQNT's local encrypted message database (`nt_msg.db`) and recover chat
history into Hermes's `corpus_messages` table. Use when `state.db` is corrupted,
lost, or when NapCat's `get_group_msg_history` API is insufficient.

## Scope

- One-time decryption of QQ's local `nt_msg.db`
- Query and export group message history
- Import missing messages into Hermes's `corpus_messages`

## Security Rules (MANDATORY)

1. **Passphrase**: obtained at runtime from NapCat's `core.dbPassphrase` via a
   plugin — NEVER logged, NEVER saved to disk, NEVER stored in a config file.
2. **Decrypted DB**: contains raw private chat data. Back it up, then delete
   after recovery. Do NOT commit, do NOT back up to git.
3. **Plugin**: loads via NapCat's debug WebSocket. It only decrypts and writes
   to a temp file; no network transmission.
4. **Recovery script**: reads locally, maps to Hermes tables, deletes temp file
   when done. **Back up the decrypted DB before running recovery.**

## Prerequisites

- NapCat running (Shell or Framework mode)
- NapCat debug plugin enabled (port `8998`, localhost-only)
- Python 3.9+ (stdlib `sqlite3`)

---

## Critical: NapCat v4+ Plugin Whitelist

NapCat v4+ has a **hardcoded plugin whitelist** in `napcat.mjs`. The `napcat-plugin-dbexport`
and `napcat-plugin-debug` plugins are rejected with `"not in official plugin whitelist"`
unless added.

### Fix: patch the whitelist

In `napcat.mjs`, find the whitelist definition (around line 64502):

```javascript
const uO = uF(import.meta.url), rme = /* @__PURE__ */ new Set([
  "napcat-plugin-builtin",
  "napcat-plugin-cleaner",
  "napcat-plugin-ssqq",
  "napcat-plugin-qce"
]),
```

Add the debug and dbexport plugins:

```javascript
const uO = uF(import.meta.url), rme = /* @__PURE__ */ new Set([
  "napcat-plugin-builtin",
  "napcat-plugin-cleaner",
  "napcat-plugin-ssqq",
  "napcat-plugin-qce",
  "napcat-plugin-debug",
  "napcat-plugin-dbexport"
]),
```

**Requires NapCat restart** to take effect. Kill the xvfb-run parent process and restart.

### Finding napcat.mjs

The running instance's napcat.mjs is at:
```
~/Napcat/opt/QQ/resources/app/app_launcher/napcat/napcat.mjs
```
Note: this is NOT the same as `~/ai/ai/NapCat.Shell/napcat.mjs` (which may be an
older/unused copy). Check the running process to find the correct path:
```bash
ps aux | grep 'qq.*3560998016' | grep -v grep
# Look for: /home/ji/Napcat/opt/QQ/qq --no-sandbox -q 3560998016
```

---

## Critical: ESM Compatibility

The dbexport plugin uses `require('os').homedir()` which **fails** under NapCat's
ESM module system. The plugin must use ESM imports instead.

### Fix: ensure the plugin uses ESM imports

The plugin source at `scripts/qq-db-export-plugin/index.mjs` must:
1. Import `homedir` at the top: `import { homedir } from 'os';`
2. Use `homedir()` in the code body, NOT `require('os').homedir()`

Wrong:
```javascript
const homeQQ = path.join(require('os').homedir(), '.config', 'QQ');
```

Correct:
```javascript
import { homedir } from 'os';
// ...
const homeQQ = path.join(homedir(), '.config', 'QQ');
```

---

## Quick Start

### 1. Auto-detect paths

```bash
# QQ data dir (auto-detect)
QQ_DIR=$(ls -d ~/.config/QQ/nt_qq_* 2>/dev/null | head -1)
DB_PATH="$QQ_DIR/nt_db/nt_msg.db"

# Hermes home
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

# NapCat plugins dir (varies by install type — check running process)
NAPCAT_PLUGINS=$(find ~/Napcat -path "*/app_launcher/napcat/plugins" -type d 2>/dev/null | head -1)
```

### 2. Check for existing decrypted DB

If the plugin was run previously, a decrypted DB may already exist:

```bash
ls -lh "$NAPCAT_PLUGINS/napcat-plugin-dbexport/nt_msg_decrypted.db"
```

If it exists and is valid SQLite (use `file` to check), skip to step 6.
If it exists but is corrupted, delete it and continue.

### 3. Backup the encrypted database

```bash
mkdir -p "$HERMES_HOME/backups"
cp "$DB_PATH" "$HERMES_HOME/backups/nt_msg_encrypted_$(date +%Y%m%d_%H%M%S).db"
```

### 4. Patch whitelist and restart NapCat

If the whitelist hasn't been patched yet (step in "Critical" section above):

```bash
# 1. Patch napcat.mjs (add napcat-plugin-debug + napcat-plugin-dbexport to rme Set)
# 2. Kill NapCat:
XVPID=$(pgrep -f 'xvfb-run.*qq' 2>/dev/null)
kill -9 $XVPID 2>/dev/null
pkill -9 -f '3560998016' 2>/dev/null
sleep 3

# 3. Restart with log capture (consoleLog=true, fileLog=false):
nohup /bin/xvfb-run -a /home/ji/Napcat/opt/QQ/qq --no-sandbox -q 3560998016 \
  > /tmp/napcat-boot.log 2>&1 &

# 4. Wait for NapCat to fully start (~30s)
sleep 30
```

### 5. Deploy and load the dbexport plugin

```bash
# Deploy plugin
cp -r "$HERMES_HOME/scripts/qq-db-export-plugin" "$NAPCAT_PLUGINS/napcat-plugin-dbexport"

# Load/reload via debug WebSocket (port 8998, auth disabled by default)
# Note: the debug WS sends a "welcome" message first — read it before sending commands
python3 -c "
import asyncio, json, websockets, os
async def main():
    async with websockets.connect('ws://127.0.0.1:8998', open_timeout=5) as ws:
        await ws.recv()  # skip welcome
        await ws.send(json.dumps({'jsonrpc':'2.0', 'method':'reloadPlugin', 'params':['napcat-plugin-dbexport'], 'id':1}))
        resp = await asyncio.wait_for(ws.recv(), timeout=10)
        data = json.loads(resp)
        if data.get('result') == True:
            await asyncio.sleep(8)  # wait for plugin to decrypt
            db = '$NAPCAT_PLUGINS/napcat-plugin-dbexport/nt_msg_decrypted.db'
            if os.path.exists(db):
                print(f'OK: {os.path.getsize(db)/1024/1024:.1f} MB')
            else:
                print('FAIL: DB not created')
        else:
            print(f'Error: {resp}')
asyncio.run(main())
"

# Check plugin logs
grep 'dbexport\|DBExport' /tmp/napcat-boot.log | tail -5
```

### 6. Backup decrypted DB BEFORE recovery

The recovery script **deletes** the decrypted DB on success. Back it up first:

```bash
cp "$NAPCAT_PLUGINS/napcat-plugin-dbexport/nt_msg_decrypted.db" \
  "$HERMES_HOME/backups/nt_msg_decrypted_$(date +%Y%m%d_%H%M%S).db"
```

### 7. Run recovery

```bash
python3 "$HERMES_HOME/scripts/qq-db-recover.py" "$NAPCAT_PLUGINS/napcat-plugin-dbexport/nt_msg_decrypted.db"
```

The script will:
- Query the decrypted DB for messages missing from `corpus_messages`
- Insert with `message_id` dedup (safe to run multiple times)
- Delete the decrypted DB file on success
- Print per-group import counts

### 8. Backup state.db and finalize

```bash
cp "$HERMES_HOME/state.db" "$HERMES_HOME/state.db.bak.$(date +%Y%m%d_%H%M%S)"
```

## Database Schema (QQNT `nt_msg.db` → `group_msg_table`)

Column names are QQ-internal integer IDs:

| Column | Content | Type |
|--------|---------|------|
| `[40001]` | message_id | INTEGER |
| `[40002]` | sender_uin | INTEGER |
| `[40021]` | group_id | TEXT (numeric) |
| `[40050]` | msg_time | INTEGER (Unix seconds) |
| `[40090]` | text content | TEXT |
| `[40800]` | message segments | BLOB (protobuf) |

## Decryption Algorithm

```
QQ_NT DB header (1024 bytes)
  ↓ skip EXT_HEADER_SIZE (1024)
Salt (16 bytes) + Encrypted Pages (4096 bytes each)
  ↓ PBKDF2-SHA512(passphrase, salt, 4000 iterations)
  → AES-256-CBC key (32 bytes)
Page 1: replace header with "SQLite format 3\0" + adjust page size
Pages 2+: decrypt with AES-256-CBC (IV at end of each page)
  ↓
Plaintext SQLite database
```

## Files

| File | Purpose |
|------|---------|
| `scripts/qq-db-export-plugin/` | NapCat plugin source (decrypts nt_msg.db) |
| `scripts/qq-db-recover.py` | Recovery script (DB → corpus_messages) |
| `backups/nt_msg_encrypted_*.db` | Encrypted QQ DB backup |
| `backups/nt_msg_decrypted_*.db` | Decrypted QQ DB backup |
| `backups/state.db.bak.*` | Hermes state DB backup after recovery |

## Troubleshooting

### "not in official plugin whitelist"

NapCat v4+ blocks custom plugins. See "Critical: Plugin Whitelist" section above.
Add the plugin to `napcat.mjs` whitelist and restart.

### "require is not defined" / "homedir is not defined"

ESM compatibility issue. See "Critical: ESM Compatibility" section above.
Ensure `import { homedir } from 'os'` and use `homedir()` in the code.

### "database is locked"

- Stop the Hermes gateway first (`systemctl --user stop hermes-gateway`)
- Check if dashboard (port 8899) or other processes hold a lock
- `fuser /home/ji/.hermes/state.db` to find the locking process

### Plugin loaded but no decrypted DB created

1. Check `/tmp/napcat-boot.log` for plugin errors
2. Ensure `fileLog: false` and `consoleLog: true` in NapCat config — plugin output
   goes to stdout, captured in boot log
3. The passphrase may not be available yet — QQ login must complete first
4. Verify QQ data directory exists and is accessible

### Debug WS connection refused

- NapCat must be restarted after whitelist patch for the debug plugin to load
- Debug plugin config: port 8998, auth disabled by default
- Test: `python3 -c "import websockets; import asyncio; asyncio.run(websockets.connect('ws://127.0.0.1:8998'))"`

## Recovery Checklist

- [ ] Encrypted DB backed up
- [ ] Whitelist patched in napcat.mjs (if needed)
- [ ] NapCat restarted with log capture
- [ ] Plugin deployed and loaded via debug WS
- [ ] Decrypted DB created and verified
- [ ] Decrypted DB backed up (before recovery deletes it)
- [ ] Hermes gateway stopped (if state.db in use)
- [ ] Recovery script ran successfully
- [ ] state.db backed up
- [ ] Hermes gateway restarted

---

## Critical: NT Decryption ≠ Content Decryption

QQ NT's SQLite encryption only protects the database file itself. Once decrypted,
the message content is still in **QQ's internal protobuf format** — NOT plain text.

### Column Mapping Reality

| Column | Type | Actual Content | Notes |
|--------|------|---------------|-------|
| `[40001]` | INTEGER | message_id | Use this for dedup |
| `[40003]` | INTEGER | sender QQ number | Real QQ number |
| `[40009]` | INTEGER | valid flag | 1 = valid message |
| `[40011]` | INTEGER | message type | 2=text, 8=forward, 9=reply, 11=@mention, 5=image |
| `[40030]` | INTEGER | group_id | Use this for group filtering |
| `[40050]` | INTEGER | Unix timestamp | |
| `[40090]` | TEXT | **群名片 (unreliable)** | NOT chat text — sender card name / status messages |
| `[40093]` | TEXT | alternate text | Sometimes has content, mostly empty |
| `[40800]` | BLOB | **protobuf message body** | Contains actual chat text. Always extract from here |
| `[40850]` | INTEGER | reply target msg_seq | Links to `[40002]` of replied message |
| `[40100]` | INTEGER | direction | 0=sent by account holder, 2=received, 6=@mentioned |

### Extracting Real Text from Protobuf BLOBs

`[40090]` is unreliable (群名片). Always extract from protobuf `[40800]`.
The method: decode BLOB → strip binary noise → extract CJK fragments (>=2 chars).

```python
import sqlite3, re

def extract_text(blob):
    if not blob:
        return ""
    try:
        raw = blob.decode('utf-8', errors='ignore')
        # 清理 protobuf 二进制噪音 (hex hashes, URLs, base64)
        raw = re.sub(r'[A-F0-9]{16,}\.(jpg|png|gif|jpeg|bmp|webp)', '', raw, flags=re.I)
        raw = re.sub(r'/download\?appid=\d+&fileid=[A-Za-z0-9_\-]+&spec=\d+', '', raw)
        raw = re.sub(r'[A-Fa-f0-9]{32,}', '', raw)
        raw = re.sub(r'[A-Za-z0-9+/=]{40,}', '', raw)
        # 提取 CJK 片段 (>=2 字)
        fragments = re.findall(r'[\u4e00-\u9fff]{2,}', raw)
        return ' '.join(fragments) if fragments else ''
    except:
        return ''

db = sqlite3.connect('nt_msg_decrypted.db')
for row in db.execute("""
    SELECT [40001],[40002],[40050],[40800]
    FROM group_msg_table WHERE [40009]=1 AND [40800] IS NOT NULL
"""):
    mid, uin, ts, blob = row
    text = extract_text(blob)
    if text:
        print(f"[{uin}] {text}")
```

**覆盖率**: 178,089/182,517 (97.9%)。剩余 2.1% 是纯图片/语音，无文本。

### Full Recovery Workflow (with real text)

```bash
# Use the standalone restore script (recommended)
python3 qq_chat_restore.py <decrypted_db> [state.db]
```

The script does: protobuf CJK extraction → txt export → state.db update → backup.

Or inline:
```python
import sqlite3, re
from datetime import datetime

SRC = 'nt_msg_decrypted.db'
DST = '~/.hermes/state.db'

def extract_text(blob):
    if not blob: return ''
    try:
        raw = blob.decode('utf-8', errors='ignore')
        raw = re.sub(r'[A-F0-9]{16,}\.(jpg|png|gif|jpeg|bmp|webp)', '', raw, flags=re.I)
        raw = re.sub(r'/download\?appid=\d+&fileid=[A-Za-z0-9_\-]+&spec=\d+', '', raw)
        raw = re.sub(r'[A-Fa-f0-9]{32,}', '', raw)
        raw = re.sub(r'[A-Za-z0-9+/=]{40,}', '', raw)
        fragments = re.findall(r'[\u4e00-\u9fff]{2,}', raw)
        return ' '.join(fragments) if fragments else ''
    except: return ''

db = sqlite3.connect(SRC)
sdb = sqlite3.connect(os.path.expanduser(DST))
sdb.execute("PRAGMA journal_mode=WAL")

for mid, uin, ts, blob in db.execute(
    "SELECT [40001],[40002],[40050],[40800] FROM group_msg_table WHERE [40009]=1 AND [40800] IS NOT NULL"
):
    text = extract_text(blob)
    if text:
        sdb.execute("UPDATE corpus_messages SET content_readable=? WHERE message_id=?", (text, str(mid)))
sdb.commit()
```
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  