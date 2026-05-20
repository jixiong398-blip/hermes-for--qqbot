# Hermes Memory Architecture — Discovered Behavior

## Background Self-Improvement Review
- Trigger: every ~10 conversation turns (`_memory_nudge_interval = 10` in `run_agent.py:1888`)
- Spawns a fork `AIAgent` with `enabled_toolsets=["memory", "skills"]`, `quiet_mode=True`, `suppress_status_output=True`
- Runs in background thread, never competes with main conversation
- Reviews conversation for successful memory/skill tool calls (using `_summarize_background_review_actions`)
- Emits: `💾 Self-improvement review: {summary}` where summary joins actions like "User profile updated" / "Memory updated"
- Source: `run_agent.py` lines 4055–4234, triggered at line 15147

## Context Window (chat_message_buffer)
- **SQLite stores everything permanently** — no auto-cleanup despite `trim_chat_buffer()` existing in `store.py:348`
- **In-memory buffer** (`_group_buffer` in adapter) is separate from SQLite
**Context passed to AI** (default behavior):
  - Time-window: **last 5 minutes** (300 seconds)
  - Silence breakpoint: >5 min gap resets topic
  - Cap: **30 lines max, 1500 chars max**
  - Fallback (empty buffer): `get_group_msg_history` API for last 20 messages (800 char cap)
**User preference override**: This user explicitly wants NO count/char limits — "这五分钟有多少就是看多少，字数也是不管的". Only time-window filtering should apply.
- **Cleanup**: User explicitly prefers time-window cleanup over count-based — `DELETE FROM chat_message_buffer WHERE created_at < now-300` keeps only last 5 minutes. No cap on message count or character length when doing cleanup — "这五分钟有多少就是看多少".
- Source: `adapter.py` lines 1112–1154

## Memory Stores
| Store | Table/File | Contents | Cleanup |
|-------|-----------|----------|---------|
| USER.md | File | User identity, preferences | `memory(action="remove")` |
| MEMORY.md | File | Personal notes, env facts | `patch()` |
| LTM | long_term_entries | Persistent facts (all categories) | SQL DELETE |
| STM | short_term_entries | Session working memory | SQL DELETE |
| Buffer | chat_message_buffer | Full chat archive | SQL DELETE or trim_chat_buffer() |
