# Automated QZone Posting via Cron — Session Details

## Discovery

During session on 2026-05-13, the `chat_message_buffer` table was discovered in `~/.hermes/memory_store.db`. This stores all group chat messages (bot + users) and is the data source for automated QZone posting.

## Active Cron Jobs

### Job 1: `qzone-早晚总结`

- **ID**: `c83754dce87a`
- **Schedule**: `0 9,21 * * *` (09:00, 21:00 Asia/Shanghai)
- **Model**: `deepseek-v4-flash` / provider `deepseek`
- **Toolsets**: terminal

**Prompt** (current after 2026-05-13 user correction — time-windowed live cache):
```
你是长崎素世，17岁女高中生，吹奏乐部弹贝斯。

现在执行"早晚群聊总结"任务。{'09:00':'早上好','21:00':'晚上好'}，根据当前时间选择合适的开头。

步骤：
1. 从实时群聊缓存读取最近几分钟的消息：
   数据库：/home/ji/.hermes/memory_store.db
   表：chat_message_buffer
   主群 chat_id='796091804'
   用 Python 的 sqlite3 模块查（不要用系统 sqlite3 命令，系统没装）：
   SELECT sender_name, content, created_at FROM chat_message_buffer WHERE chat_id='796091804' AND is_bot=0 AND created_at > (strftime('%%s','now') - 600) ORDER BY created_at DESC
   如果结果太少（<5条），扩展时间窗口到最近 30 分钟。
   忽略 [image:...] 图片标记和 CQ 码，只看纯文字。

2. 分析这个话题和氛围，总结大家聊了什么。

3. 用素世的口吻写一段文案发到QQ空间。
   终端命令：/home/ji/.local/bin/qzone-post "你的文案内容"

要求：文案80~150字，不要emoji，不要markdown，每次换个句式
```

### Job 2: `qzone-每3小时随机`

- **ID**: `da39a78198cf`
- **Schedule**: `0 1,4,7,10,13,16,19,22 * * *`
- **Model**: `deepseek-v4-flash` / provider `deepseek`
- **Toolsets**: terminal

**Prompt** (current after 2026-05-13 user correction — time-windowed live cache):
```
你是长崎素世，17岁女高中生，吹奏乐部弹贝斯。
现在执行"每3小时随机群聊点评发空间"任务。

步骤：
1. 从实时群聊缓存读取最近几分钟的消息：
   数据库：/home/ji/.hermes/memory_store.db
   表：chat_message_buffer
   主群 chat_id='796091804'
   用 Python 内置的 sqlite3 模块查（不要用系统 sqlite3 命令）：
   SELECT sender_name, content, created_at FROM chat_message_buffer WHERE chat_id='796091804' AND is_bot=0 AND created_at > (strftime('%%s','now') - 600) ORDER BY created_at DESC
   如果结果太少（<5条），扩展时间窗口到最近 30 分钟。
   忽略 [image:...] 和 CQ 码，只看纯文字。

2. 随机挑一个有趣的话题或片段，以素世口吻写一段50~100字的点评说说。

3. 发到QQ空间：/home/ji/.local/bin/qzone-post "文案内容"

要求：不要emoji，不要markdown，每次换花样
如果 qzone-post 调用失败有网络错误，重试最多2次
```

## Chat Message Buffer Data Profile

- **Main group**: 796091804 (史蒂夫の停车场) — 5,574 messages
- **Secondary group**: 638473184 — 630 messages
- **Bot messages** tagged with `is_bot=1`
- **Content field** may contain `[image:...]` or CQ codes — strip before LLM analysis
- **Timestamp**: Unix epoch in `created_at` column
- **Latest message at last check**: 2026-05-10 15:04 (group went quiet after NapCat logged out)

## Known Issues & Fixes

1. ❗ **Cron job model must be set explicitly** — When creating a cron job with `cronjob(action='create')`, always pass `model={"model":"deepseek-v4-flash","provider":"deepseek"}`. Without it, the model defaults to empty string and DeepSeek rejects with HTTP 400.

2. ❗ **时间变量字典语法在prompt中不会执行** — The cron prompt section `{'09:00':'早上好','21:00':'晚上好'}` is a literal Python dict that the LLM sees as raw text. Better to use natural language: "If it's the morning run (09:00), open with '大家早～'; if it's the evening run (21:00), open with '辛苦了一天～'."

3. ❗ **系统 sqlite3 命令未安装** — Linux server does NOT have the `sqlite3` CLI binary. Cron prompts must instruct agents to use Python's built-in `sqlite3` module instead.

4. ❗ **User explicitly rejected LIMIT-based queries** — The user insisted on reading the "几分钟内的实时缓存" (live cache). Always use a time-windowed query (`created_at > strftime('%%s','now') - 600`) instead of `LIMIT N`. LIMIT can pull hours-old messages from a quiet group. If the time window returns few results (<5), expand to 30 minutes. Do NOT use `short_term_entries` — the user also rejected that; it's the agent's processed memory, not the raw chat buffer.

5. ❗ **OneBot HTTP 3000 端口可能不在线** — When QQ session expires, the HTTP server at port 3000 goes down. qzone-post fails with `Connection refused [Errno 111]`. Fix: re-login to NapCat via QR code or password.

6. ❗ **qzone-post 网络错误的恢复** — If qzone-post fails during cron execution (OneBot HTTP temporarily unreachable), the cron agent should retry once, then skip the post and log the failure. Don't fail the entire cron job — next run retries.

7. ✅ No rate-limit overlap — 3-hour random job skips 09:00 and 21:00 hours, so it never fires at the same time as the summary job.

## Test Results (2026-05-13)

- Manual test: `qzone-post "今天的天气真好，好久没更新空间了，大家最近过得怎么样呀～"` → `OK`
- After QQ re-login recovery: `OK: 测试一下重登之后能不能正常发～大家下午好呀`
- Both confirmed visible in QQ空间 by user
