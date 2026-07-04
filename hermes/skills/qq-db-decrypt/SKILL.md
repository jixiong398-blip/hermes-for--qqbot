# QQ Database Decrypt

## Purpose

Decrypt QQNT's local encrypted message database (`nt_msg.db`) and recover chat
history into Hermes's `corpus_messages` table. Use when NapCat's
`get_group_msg_history` API is insufficient (capped at ~25,000 messages) or when
Hermes's persist worker failed and messages went missing.

## Scope

- One-time decryption of QQ's local `nt_msg.db`
- Query and export group message history
- Import missing messages into Hermes's `corpus_messages`

## Security Rules (MANDATORY)

1. **Passphrase**: obtained at runtime from NapCat's `core.dbPassphrase` via a
   plugin — NEVER logged, NEVER saved to disk, NEVER stored in a config file.
2. **Decrypted DB**: contains raw private chat data. Delete immediately after
   recovery (`rm`). Do NOT commit, do NOT back up.
3. **Plugin**: loads via NapCat's local-only debug WebSocket. It only decrypts
   and writes to a temp file; no network transmission.
4. **Recovery script**: reads locally, maps to Hermes tables, deletes temp file
   when done.

## Prerequisites

- NapCat running (Shell or Framework mode)
- Database passphrase obtained (`grep "数据库辅助支持" $NAPCAT_LOG`)
- NapCat debug plugin enabled (default port `8998`, localhost-only)
- Python 3.9+ (stdlib `sqlite3`)

## Quick Start

### 1. Auto-detect paths

```bash
# QQ data dir (auto-detect)
QQ_DIR=$(ls -d ~/.config/QQ/nt_qq_* 2>/dev/null | head -1)
DB_PATH="$QQ_DIR/nt_db/nt_msg.db"

# Hermes home
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"

# NapCat plugins dir (varies by install type)
NAPCAT_PLUGINS=$(find ~/Napcat -path "*/app_launcher/napcat/plugins" -type d 2>/dev/null | head -1)
```

### 2. Deploy the dbexport plugin

Copy the plugin source from Hermes scripts to NapCat:

```bash
cp -r "$HERMES_HOME/scripts/qq-db-export-plugin" "$NAPCAT_PLUGINS/napcat-plugin-dbexport"
```

### 3. Load the plugin via debug WebSocket

```bash
# Connect to NapCat debug WS (default: ws://127.0.0.1:8998)
# Get port from: grep "调试服务已启动" $NAPCAT_LOG
DEBUG_PORT=${NAPCAT_DEBUG_PORT:-8998}

node -e "
const ws = new (require('ws'))('ws://127.0.0.1:$DEBUG_PORT');
ws.on('open', () => {
  ws.send(JSON.stringify({jsonrpc:'2.0', method:'reloadPlugin',
    params:['napcat-plugin-dbexport'], id:1}));
  ws.on('message', d => { console.log(d.toString()); ws.close(); });
});
"
```

Check output: `[DBExport] Decrypted: <size> bytes → <path>`

### 4. Run recovery

```bash
python3 "$HERMES_HOME/scripts/qq-db-recover.py" <path-to-decrypted-db>
```

The script will:
- Query the decrypted DB for messages missing from `corpus_messages`
- Insert with `message_id` dedup
- Delete the decrypted DB file on success

### 5. Cleanup

```bash
# Unload the plugin (optional, it's a no-op after init)
node -e "
const ws = new (require('ws'))('ws://127.0.0.1:$DEBUG_PORT');
ws.on('open', () => ws.send(JSON.stringify({jsonrpc:'2.0',
  method:'setPluginStatus', params:['napcat-plugin-dbexport',false], id:1})));
ws.on('message', d => { console.log(d.toString()); ws.close(); });
"
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

Inlined from NapCat's `napcat.mjs`:

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
| Temp: `nt_msg_decrypted.db` | Decrypted output (**DELETE AFTER USE**) |

## Recovery Checklist

- [ ] Passphrase confirmed via NapCat log
- [ ] Plugin deployed and loaded
- [ ] Decrypted DB obtained
- [ ] Corpus messages imported
- [ ] Decrypted DB deleted (`rm`)
- [ ] Plugin unloaded or left as no-op
