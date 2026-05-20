---
name: qq-napcat-management
description: Start, stop, restart, and manage NapCat QQ bot on Linux (xvfb-run + QR code login). Covers process control, config inspection, QR code capture, and image delivery via Feishu.
category: devops
tags: [napcat, qq-bot, onebot, linux, xvfb, qr-login]
---

# NapCat QQ Bot Management (Linux)

## Triggers
- User says "重启napcat", "重启QQ", "napcat死了", "二维码", "扫码登录"
- Gateway reports NapCat disconnected / WS timeout
- Need to re-login after token expiration

## Quick Reference

### Process Layout
- Base dir: `/home/ji/Napcat/`
- QQ binary: `/home/ji/Napcat/opt/QQ/qq`
- Config (OneBot网络): `/home/ji/.napcat/config/onebot11_{{BOT_QQ_ID}}.json`
- Config (NapCat内核/防检测): `/home/ji/Napcat/opt/QQ/resources/app/app_launcher/napcat/config/napcat_{{BOT_QQ_ID}}.json`
- Log: `/tmp/napcat.log`
- QR code image: `/home/ji/Napcat/opt/QQ/resources/app/app_launcher/napcat/cache/qrcode.png`
- PID file: `/home/ji/Napcat/run/napcat.pid`
- Bot QQ: {{BOT_QQ_ID}}

### Starting Without xvfb (Laptop with Display)

If the server is actually a laptop with a desktop environment (GNOME/KDE etc.), xvfb is technically optional:

```bash
cd ~/Napcat && /home/ji/Napcat/opt/QQ/qq --no-sandbox -q {{BOT_QQ_ID}} &>/tmp/napcat.log &
```

This will pop up a real QQ NT window. **However**, keeping xvfb is recommended for a bot — no stray window cluttering the desktop, and the bot runs cleanly in the background regardless of whether a desktop session is active.

### Start NapCat
```bash
# Use background=true for the terminal call — this is a long-lived process
cd ~/Napcat && /bin/xvfb-run -a --server-args="-screen 0 800x600x24" /home/ji/Napcat/opt/QQ/qq --no-sandbox -q {{BOT_QQ_ID}} &>/tmp/napcat.log &
```

`xvfb-run -a` auto-assigns the first free X display. The display number will show in `ps aux | grep xvfb`.

### Stop NapCat
Find PIDs with `ps aux | grep -i napcat | grep -v grep`, then `kill -9 <pids>`. Kill both the QQ process and its xvfb child.

```bash
# Get all NapCat/QQ process PIDs
ps aux | grep -i napcat | grep -v grep | awk '{print $2}'
# Kill them
kill -9 <PID1> <PID2> ...
```

### Check Status
```bash
ps aux | grep -i napcat | grep -v grep
# Expected: xvfb-run + Xvfb + qq processes
```

### Discover Active Log File
NapCat may log to different paths across restarts. Don't guess — follow the process:
```bash
# Find the main QQ process PID
PID=$(ps aux | grep "Napcat/opt/QQ/qq --no-sandbox" | grep -v grep | head -1 | awk '{print $2}')
# Follow stdout to find the active log
readlink /proc/$PID/fd/1
```
Typical paths: `/tmp/napcat.log` (early init) or `/home/ji/Napcat/log/napcat_{{BOT_QQ_ID}}.log` (runtime).

### Successful Login Indicator
A successfully logged-in NapCat will show real-time group chat traffic in its log:
```
接收 <- 群聊 [史蒂夫の停车场(796091804)] [...]
```
Looking for `接收 <- 群聊` or `接收 <- 私聊` lines means the bot is online. If you only see QR code output and no message flow, login hasn't completed yet.

### Verify Gateway Connectivity
Beyond NapCat itself, check that the Hermes Gateway OneBot connection is alive:
- OneBot WS is at `ws://127.0.0.1:3001/onebot/v11/ws`
- The Gateway log at `~/.hermes/logs/gateway.log` will show connection state
- If NapCat is running but Gateway shows no connection, restart the Gateway

Also check the WebUI: `http://127.0.0.1:6099/webui?token=...` (token in log).

### QR Code Login
After starting, NapCat will print a QR code to the log and save it as PNG. Steps:
1. Wait ~8-10 seconds for startup (check log: `tail -50 /tmp/napcat.log`)
2. Look for the line: `二维码已保存到 ... qrcode.png`
3. The image is at the path mentioned above (see Quick Reference)
4. Send via Feishu: `send_message(target="feishu:oc_492396292bf59e9cf911af09e275fe85", message="MEDIA:/path/to/qrcode.png\n说明文字")`
5. User scans the QR code to authorize the bot

### Config Details
- HTTP Server: `127.0.0.1:3000`
- WebSocket Server: `127.0.0.1:3001`
- Token is in the config file but masked in AGENTS.md

## NapCat 协议架构与防检测配置

### 协议架构说明

NapCat 基于 **QQ NT 架构**（统一跨平台协议栈），当前运行的是 **QQ Windows 版** 3.2.25-45758，通过 xvfb 虚拟显示在 Linux 上运行。

**"切换 QQLinux 协议"的真相**：QQ NT 在 Windows / Linux / macOS 上使用的是同一套底层协议栈（腾讯 UnifiedProtocol），不存在"切换协议"这个概念。所谓"换成 QQLinux"实际指的是：
- 换用 QQ Linux 原生客户端二进制作为 NapCat 的底层载体
- 而不是真的切换通信协议

**QQLinux 方式的潜在好处**：
- 不需要 xvfb 模拟显示，Linux 上直接跑原生 QQ
- 设备指纹可能和 Windows QQ 不同（偶尔能绕过一些风控）

**QQLinux 方式的风险**：
- QQ Linux 版功能比 Windows 版少，更新频率低
- 腾讯对 Linux 版的支持和维护力度不如 Windows 版
- 稳定性可能更差

**结论**：在排查被踢下线问题时，先检查 bypass 配置和风控原因，不要优先考虑换 QQ 客户端版本。

### Bypass 防检测配置

防检测配置在 NapCat 内核配置文件中：
`/home/ji/Napcat/opt/QQ/resources/app/app_launcher/napcat/config/napcat_{{BOT_QQ_ID}}.json`

```json
{
    "o3HookMode": 3,
    "bypass": {
        "hook": true,
        "window": true,
        "module": true,
        "process": true,
        "container": true,
        "js": true
    }
}
```

**各字段含义**：
| 字段 | 说明 | 推荐值 |
|------|------|--------|
| `o3HookMode` | O3 hook 强度 (1-3)，3 最高 | `3` |
| `bypass.hook` | 禁用 QQ 的 hook 检测 | `true` |
| `bypass.window` | 窗口层面防检测 | `true` |
| `bypass.module` | 模块注入防检测 | `true` |
| `bypass.process` | 进程层面防检测 | `true` |
| `bypass.container` | 容器环境防检测 | `true` |
| `bypass.js` | JS 注入防检测 | `true` |

**注意**：NapCat 自身还有一个全局配置 `/home/ji/Napcat/opt/QQ/resources/app/app_launcher/napcat/config/napcat.json`，里面的 bypass 默认都是 `false` — 这个文件是兜底默认值，实际生效的是带 QQ 号的配置文件（`napcat_{{BOT_QQ_ID}}.json`）。修改时要改带 QQ 号的那个。

## Troubleshooting: 被踢下线 (KickedOffLine)

### 常见原因

1. **帐号安全策略**：笔记本 IP 跟手机使用 IP 不同（异地登录），QQ 安全中心触发保护
2. **消息频率过高**：Bot 短时间内大量发消息，触发风控
3. **设备指纹异常**：xvfb 模拟的 Windows QQ 环境可能有异常的客户端指纹特征
4. **登录 token 过期**：表现为"快速登录失败"而非"被踢"
5. **多处登录冲突**：同账号在不同设备上频繁切换

### 排查步骤

1. **检查 watchdog 日志**：看 `napcat_{{BOT_QQ_ID}}.log` 中的实际错误信息
   ```bash
   grep -i "kicked\|KickedOffLine\|踢下线" /home/ji/Napcat/log/napcat_{{BOT_QQ_ID}}.log
   ```
2. **确认 bypass 全开**：检查 `napcat_{{BOT_QQ_ID}}.json` 中 bypass 是否全部 `true`
3. **检查消息频率**：看 SQLite buffer 中 bot 最近的消息数量
4. **检查 QQ 安全中心**：登录手机 QQ 查看是否有安全提醒
5. **尝试更换 QQ 版本**（最后的手段）：如果 bypass 全开后仍然频繁被踢，可以考虑换用 QQ Linux 原生版

### 与"快速登录失败"的区别

| 现象 | 日志关键词 | 含义 | 处理 |
|------|-----------|------|------|
| 被踢下线 | `KickedOffLine` / `被踢下线` | 在线时被 QQ 服务器强制断开 | 检视风控原因，登出重登 |
| 快速登录失败 | `快速登录失败` / `快速登录错误` | 重启后 session token 失效 | 需要重新扫码/密码登录 |

### Feishu Message Target
- DM with user: `feishu:oc_492396292bf59e9cf911af09e275fe85`
- No home channel set for Feishu — always use explicit target

### Retrying Failed QZone Cron Jobs (User Requested)

When a cron job reports failure because OneBot is offline ("OneBot 服务未运行，qzone-post 无法连接"), and the user asks to retry:

1. **First verify OneBot is back up** — `curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:3000/` — HTTP 403 means service is running (returns 403 because no auth token); connection refused or timeout means still down
2. **Read the failed run's output** from `~/.hermes/cron/output/<job_id>/<timestamp>.md` — the Response section contains the generated QZone content
3. **Don't rely solely on `cronjob(action="run")`** — manual cron triggers may NOT produce a new output file or update `last_run_at`/`last_status`. The agent session runs in background and output may be lost
4. **Most reliable: run qzone-post directly** once OneBot is confirmed up:
   ```bash
   /home/ji/.local/bin/qzone-post "内容（从失败的输出日志中提取）"
   ```
5. **If OneBot is still down**, tell the user to re-login NapCat first, then retry

**Pitfalls:**
- ❗ `cronjob(action="run")` triggers a new agent session but its delivery target is `"origin"` (the original creator of the cron job). On Linux Gateway, the output may not surface naturally — always fall back to direct `qzone-post` execution
- ❗ The failed run's content is in the `Response` section of the cron output markdown file — read it with `read_file` before retrying
- ❗ If OneBot was down and now came back, the cookie session is still valid (it's NapCat's active session) — no need to re-login for qzone-post

### QZone (QQ空间) Posting via NapCat Login

NapCat's already-logged-in QQ session can be used to post to QQ空间 (QZone) — no additional login needed. The technique hijacks the QQ session cookie from NapCat's OneBot API.

**Script**: `/home/ji/.local/bin/qzone-post`

**Three-step mechanism**:
1. **Get cookies** — `POST /get_cookies` to OneBot HTTP with `{"domain": "qzone.qq.com"}` → returns session cookies containing `skey` and `uin`
2. **Calculate g_tk** — QQ's CSRF token, computed from skey via a hash: `h=5381; for c in skey: h += (h << 5) + ord(c); return h & 0x7fffffff`
3. **POST to QZone API** — `POST https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_publish_v6?g_tk=<gtk>&qzreferrer=https://user.qzone.qq.com/<uin>` with `Cookie` header and form data `{con, feedversion, ver, hostuin, format, code_version}`

**Usage**:
```bash
qzone-post "今天的天气真好～大家最近过得怎么样呀"
```

**Output on success**: `OK: 今天的天气真好～大家最近过得怎么样呀...`
**Output on failure**: `FAIL: <error message>`

**Logging**: Successful posts are logged to `~/.hermes/memory_store.db` (table `long_term_entries`, category `qzone`).

**Pitfalls**:
- ❗ OneBot HTTP must be reachable at `http://127.0.0.1:3000` with the correct Authorization token
- ❗ NapCat must be **actively logged in** — the cookie session is tied to the current QQ login session
- ❗ If NapCat restarts, the session cookies change — must re-fetch cookies before posting
- ❗ The `uin` from cookie has an `o` prefix (e.g. `o{{BOT_QQ_ID}}`) — the script strips it automatically
- ❗ QZone API rate-limits: don't post too frequently (QQ may flag as spam)
- ❗ QZone content supports plain text only via this API — no images, no rich media formatting through this simple POST approach
- ❗ **DB lock causes duplicate QZone posts!** The `post_mood()` function posts to QZone API first (lines 36-40), THEN logs to DB (lines 43-47). If the DB is locked by Hermes agent (`database is locked`), the QZone post succeeds but the script crashes on DB write → the agent sees a FAIL error and retries → each retry posts AGAIN to QZone → identical duplicate posts. Fix: wrap the DB write in a `try/except` so a DB lock only logs a warning, not a crash. The agent script (`qzone-post`) has already been patched with this fix as of 2026-05-20.
- ❗ The memory store DB may not have the `long_term_entries` table created yet on first run — the script handles this implicitly via SQLite auto-create, but the table will only exist after the first successful post

See `references/qzone-posting.md` for the full script source and session-specific details.

### Decoded URL Alternative
If the QR image approach fails, extract the `二维码解码URL` from the log line. The URL looks like:
```
https://txz.qq.com/p?k=<base64>&f=1600001615
```
Opening this URL on the user's phone browser will trigger QQ app to intercept and show the authorization page.

### WebUI Login
NapCat WebUI runs on port 6099:
```
http://<server-ip>:6099/webui?token=<token>
```
The token is in `~/.napcat/config/webui.json` key `"token"`, or printed in log: `WebUi Token: 51ff48e4aa6e`.

The WebUI can:
- Display a fresh QR code for scanning
- Accept password-based login
- Show bot connection status

### Expose WebUI via Tunnel (when user can't access localhost)
Install and use localtunnel to expose port 6099 publicly:
```bash
npm install -g localtunnel
npx localtunnel --port 6099 > /tmp/lt_output.txt 2>&1
# Wait ~5s, then check /tmp/lt_output.txt for the public URL
```
However, this approach is unreliable — localtunnel may not produce output promptly. Alternative: ask user to SSH with `-L` forwarding if they have shell access.

### Password Environment Variables (avoid QR entirely)
To skip QR code login, set password env vars before starting:
```bash
env NAPCAT_QUICK_PASSWORD="your_qq_password" NAPCAT_QUICK_PASSWORD_MD5="" /bin/xvfb-run -a --server-args="..." /home/ji/Napcat/opt/QQ/qq --no-sandbox -q {{BOT_QQ_ID}}
```
The log will say: `建议优先使用 ACCOUNT + NAPCAT_QUICK_PASSWORD（NAPCAT_QUICK_PASSWORD_MD5 作为备用）` if these are unset.

### Password + SMS Verification Flow
Even when `NAPCAT_QUICK_PASSWORD` is correctly detected, QQ may still require SMS/captcha verification on first login after token expiry. The log will show:
```
检测到 NAPCAT_QUICK_PASSWORD，已在内存中计算 MD5 用于回退登录
正在尝试密码回退登录 {{BOT_QQ_ID}}
正在密码登录 {{BOT_QQ_ID}}
需要验证码, proofWaterUrl: https://ti.qq.com/safe/tools/captcha/sms-verify-login?...
密码回退需要验证码，请在 WebUi 中继续完成验证
```

The proofWaterUrl is a OneClick link meant to open QQ app on mobile — this does not work reliably. QQ app fails to intercept the redirect properly on most phones. The practical workarounds:

1. **WebUI is the only reliable path**: The log explicitly says "请在 WebUi 中继续完成验证". Expose port 6099 via tunnel so the user can open it in their desktop browser and complete the SMS verification from there.
2. **Restart caveat**: If you restart NapCat between verification attempts, the session SID changes and the user must verify again. Always complete verification on the SAME running instance.
3. **No CLI workaround**: There is no API to feed the SMS code back — it is strictly a WebUI/browser flow.
4. **QR code fallback**: If password+SMS fails, kill the process and restart WITHOUT the password env var (pure QR mode).

### Screenshot Capture from xvfb
When debugging NapCat display state, you may need to capture the xvfb virtual display:
- `import` (ImageMagick) is NOT available on this system — only ImageMagick common libs
- Alternative: `ffmpeg -f x11grab` to capture frames
- But: xvfb captures are tiny (~3KB), mostly blank/minimal — not useful for reading QR codes or UI state
- For QR codes: always use `cache/qrcode.png` instead of screenshot

```bash
XAUTHORITY=/tmp/xvfb-run.XXXX/Xauthority DISPLAY=:N ffmpeg -y -video_size 800x600 -f x11grab -i :N -vframes 1 /tmp/screen.png
```

### Automated QZone Posting via Cron (Group Chat Driven)

Two cron jobs are configured to post to QQ空间 automatically, pulling content from group chat history:

| Job | Schedule | Behavior |
|-----|----------|----------|
| `qzone-早晚总结` | `0 9,21 * * *` (09:00 & 21:00) | Summarize recent group chats, pick 1-2 interesting topics, write a commentary post |
| `qzone-每3小时随机` | `0 1,4,7,10,13,16,19,22 * * *` | Random topic pick from recent group chats, short casual post |

**Both jobs read from the same live-cache data source:**

```sql
-- memory_store.db → table: chat_message_buffer
-- ⚠️ Use TIME WINDOW not LIMIT — user explicitly corrected this.
-- LIMIT can pull hours-old messages from an inactive group.
-- The user wants only the "几分钟内的实时缓存" (live cache of recent minutes).

SELECT sender_name, content, created_at
FROM chat_message_buffer
WHERE chat_id='796091804' AND is_bot=0
  AND created_at > (strftime('%%s','now') - 600)  -- last 10 minutes
ORDER BY created_at DESC;

-- If fewer than 5 results, fall back to 30-minute window:
-- AND created_at > (strftime('%%s','now') - 1800)
```

**Why time window instead of LIMIT**: The user explicitly rejected both `LIMIT N` (can pick up stale messages from hours ago) and querying `short_term_entries` (agent's processed memory, not raw chat buffer). The `chat_message_buffer` table IS the real-time buffer — messages land here immediately when the Gateway receives them. Adding a time window filter makes the cron job read only the "近几分钟的缓存" (recent-minutes cache).

**`chat_message_buffer` table schema:**
| Column | Type | Notes |
|--------|------|-------|
| id | INTEGER | Primary key |
| chat_id | TEXT | QQ group number (796091804 = main group) |
| chat_type | TEXT | 'group' or 'private' |
| user_id | INTEGER | QQ user ID |
| sender_name | TEXT | Display name in group |
| content | TEXT | Message text; may contain `[image:path]` markers |
| is_bot | INTEGER | 1 = bot's own message, 0 = user message |
| created_at | REAL | Unix timestamp |
| message_id | TEXT | NapCat message ID for referencing |

**Cron job prompt pattern:**

The prompts should instruct the agent to:
1. Query `chat_message_buffer` for recent messages, always using **Python's built-in sqlite3 module** (`python3 -c "..."`) — the `sqlite3` CLI binary is NOT installed on this system
2. Ignore `[image:...]` markers (only analyze text)
3. Generate a character-appropriate post (素世's voice: ~ sentence endings, slightly elegant but casual)
4. Call `/home/ji/.local/bin/qzone-post "content"` to publish

**Important**: Both jobs require the `terminal` toolset enabled (to run SQL queries and call qzone-post). The `web` toolset is NOT needed — all data is local.

**⚠️ LLM 经常跳过执行步骤**：LLM 在 cron 任务中经常只生成文案文字就结束，不实际调用 `qzone-post` 脚本。任务显示 `completed successfully` 但空间没发出去。修复方法：在 prompt 中加强调语「**必须调用 terminal 工具执行以下命令来实际发布，不能只生成文字**」并附上 `❗如果不执行 terminal，说说不会真的发出去！` 等警告。

**Pitfalls**:
- ❗ **User explicitly wants time-windowed reads, not LIMIT-based reads for cron jobs.** LIMIT N can pull stale messages from hours/days ago when the group is quiet. Always query with `created_at > (strftime('%%s','now') - 600)` and fall back to 1800 if <5 results. Do NOT use `short_term_entries` — that's the agent's memory, not the raw chat buffer.
- ❗ **QZone posting 依赖 OneBot HTTP 连接。** qzone-post 脚本通过 OneBot HTTP API (127.0.0.1:3000) 获取 QQ 会话 cookie。如果 OneBot 未连接（NapCat 重启后 Gateway 未重连等），qzone-post 拿不到 cookie，发空间会失败。任务显示 completed successfully 但实际空间没发出去——因为 LLM session 跑完了但 qzone-post 静默失败。
- ❗ The cron prompt above uses `{'09:00': '早上', '21:00': '晚上'}[当前时间]` as a template — this is a Python dict literal and does NOT get evaluated. Rephrase naturally like "If it's the morning run (09:00), open with a greeting; if it's the evening run (21:00), wrap up warmly."

See `references/qzone-cron-posting.md` for the full cron job prompts and session-specific details.

See `references/send-image-via-http-api.md` for sending images through NapCat's HTTP API (curl to port 3000 with `file://` path) — the `send_message` MEDIA prefix is NOT supported on OneBot.

## QQ Connection Watchdog

A cron-based watchdog monitors NapCat's connection state and notifies the user when the bot goes offline.

**Script**: `/home/ji/.hermes/scripts/qq_watchdog.py`

### How It Works

The watchdog runs every minute via a no-agent cron job. It does two things:

1. **端口检测**: Checks ports `6099` (NapCat WebUI) and `3001` (NapCat OneBot WS)
2. **日志关键词检测**: Scans `napcat_{{BOT_QQ_ID}}.log` 从上次检查位置往后读，检测两类告警：
   - `快速登录失败` / `快速登录错误` — 重启后登录不上的情况
   - `KickedOffLine` / `被踢下线` — 在线久了被踢的情况

State is tracked in `~/.hermes/qq_watchdog_state.json`:
```json
{"online": true, "notified": false, "last_line": 42928, "at": ""}
```

- **日志告警**: 发现新错误行时输出 `⚠️ 重启后快速登录失败 (MM-DD HH:MM:SS)` 或 `⚠️ QQ被踢下线 (MM-DD HH:MM:SS)`
- **端口断线**: 连接断开时输出 `⚠️ QQ Bot 端口断线了 (HH:MM:SS)`，恢复时输出 `✅ QQ Bot 已恢复连接`
- **No output when state is stable** — the no-agent cron job stays silent, saving tokens
- **Re-notification guard** — tracks `last_line` position to avoid重复告警同一批日志

### Cron Job Definition

```bash
# Created via cronjob API:
cronjob(action="create", name="qq-watchdog", no_agent=True,
        schedule="* * * * *", script="qq_watchdog.py")
```

**Important**: The `no_agent` pattern is critical here — the script runs independently without LLM cost. It produces output ONLY when the state changes, which triggers delivery to the user.

### Testing the Watchdog

To verify log scanning works, manually reset the `last_line` to a known error position and run:

```bash
python3 -c "
import os, json
state = json.load(open('/home/ji/.hermes/qq_watchdog_state.json'))
state['last_line'] = 40280  # 设在已知的"快速登录错误"行之前
json.dump(state, open('/home/ji/.hermes/qq_watchdog_state.json', 'w'))
"
python3 /home/ji/.hermes/scripts/qq_watchdog.py
# 预期输出: ⚠️ 重启后快速登录失败 (MM-DD HH:MM:SS)
```

然后重置回当前日志末尾：

```bash
python3 -c "
import os, json
total = int(os.popen('wc -l < /home/ji/Napcat/log/napcat_{{BOT_QQ_ID}}.log').read().strip())
state = json.load(open('/home/ji/.hermes/qq_watchdog_state.json'))
state['last_line'] = total
json.dump(state, open('/home/ji/.hermes/qq_watchdog_state.json', 'w'))
"
python3 /home/ji/.hermes/scripts/qq_watchdog.py
# 无输出 = 正常
```

### Hourly Restart / Login Expiry Cycle

See `references/hourly-restart-login-expiry.md` for the complete timeline and pattern — NapCat restarts every hour on the hour, quick login fails because the session token expires, and the bot enters a 2-minute retry loop until the user re-logs in manually.

### Pitfalls
- ❗ The script must be at `~/.hermes/scripts/qq_watchdog.py` for the cronjob system to find it (uses `~/.hermes/scripts/` prefix internally)
- ❗ Port check only confirms the NapCat process is listening — it doesn't verify the Hermes Gateway connection to OneBot. For full pipeline health, check the Gateway log as well
- ❗ First run creates the state file silently (no notification — assumes OK at startup)
- ❗ No re-notification timeout currently — after a "reconnected" message, the next disconnect will trigger immediately

### OneBot 自动重连（已修复）

**NapCat 重启后 Gateway 的 OneBot 连接会自动恢复。** 参考 `references/onebot-gateway-reconnect-issue.md`。

修复内容：在 `/home/ji/.hermes/plugins/platforms/onebot/adapter.py` 的 `_ws_loop()` 中加入了自动重连逻辑：
- WebSocket 断线后等待 5 秒重连
- 失败后指数退避（10s → 20s → 40s → 60s cap）
- 重连成功后自动拉取遗漏消息
- 手动关闭 Gateway 时（`disconnect()`）设置停止标记，不卡重连循环

验证方法：
```bash
tail -f ~/.hermes/logs/gateway.log | grep -a "OneBot"
# 预期看到: [OneBot] Reconnecting in 5s... → [OneBot] Reconnected successfully
```

## Pitfalls
- ❗ **Must use `background=true`** when starting via `terminal()` — this is a long-running process, not a quick command
- ❗ **QQ blocks scanning QR codes from photo album!** Never suggest "save image → open in QQ's scan-from-album" — this does not work. QQ's camera scanner only accepts real camera input. Acceptable workarounds: (a) display the QR on a second device/screen and scan from there, (b) use the decoded URL directly in phone browser, (c) use WebUI, (d) set password env vars.
- ❗ **`env` prefix required for password env var** — `NAPCAT_QUICK_PASSWORD=xxx cd dir && cmd` does NOT pass the env var to `cmd` (scoped to `cd` only). Must use: `env NAPCAT_QUICK_PASSWORD=xxx /bin/xvfb-run ...`
- ❗ The `qrcode.png` is only generated ONCE after startup. If it expires (about 3-5 minutes), you need to restart NapCat entirely (kill + start again)
- ❗ Don't confuse the old `:99` display xvfb with the new one — the new start gets a fresh display like `:100`
- ❗ Sending images requires the explicit Feishu target, not bare `feishu` platform name (no home channel configured)
- ❗ Docker-based approach not available — this runs bare-metal with xvfb on a headless Linux server
- ❗ `launcher-user.bat` is Windows-only; Linux uses direct xvfb-run invocation
- ❗ SMS verification URL (`ti.qq.com/safe/tools/captcha/sms-verify-login?...`) cannot complete on mobile — QQ app intercept fails. Complete verification via WebUI on desktop browser instead.
- ❗ When killing NapCat, the log shows `[FATAL:electron/shell/browser/electron_browser_main_parts.cc:509] Failed to shutdown.` — this is a cosmetic Electron crash from forced process kill, not a real issue. NapCat starts fresh on next launch.
- ❗ QR code login flow for remote users: send image via Feishu → user opens on phone → user cannot scan from QQ album. Correct path: send the `二维码解码URL` instead and ask user to open in phone browser, OR have user SSH tunnel to WebUI.
- ❗ On Linux, NapCat may create TWO log files: `/tmp/napcat.log` (old init) and `/home/ji/Napcat/log/napcat_{{BOT_QQ_ID}}.log` (actual runtime log). Always check the latter for real-time status. Use `readlink /proc/<pid>/fd/1` to discover the active log path.
- ❗ Cron jobs must have model set explicitly — When creating cron jobs with cronjob(action='create'), always pass the model/provider right away: model={"model":"deepseek-v4-flash","provider":"deepseek"}. Without it, the model defaults to empty string and the API call fails with HTTP 400. This cannot be inherited from the current session — it must be set during creation.
- ❗ **修改任何平台适配器代码后必须重启 Gateway** — 无论是 OneBot adapter (`/home/ji/.hermes/plugins/platforms/onebot/adapter.py`) 还是飞书适配器 (`/home/ji/.hermes/gateway/platforms/feishu.py`) 等，修改后都需要 `hermes gateway restart` 才能生效。只重启 NapCat 不行。
- ❗ **NapCat 有两个 config 文件**：`napcat.json`（全局默认，bypass 全 false）和 `napcat_{{BOT_QQ_ID}}.json`（QQ 号专属，实际生效）。修改 bypass 要改带 QQ 号的那个，改全局默认不生效。
- ❗ **"QQLinux协议"不存在**：QQ NT 架构在 Windows/Linux/macOS 用统一协议栈。不存在"切换到 QQLinux 协议"的概念——只能换 QQ 客户端二进制文件，不能换协议本身。
