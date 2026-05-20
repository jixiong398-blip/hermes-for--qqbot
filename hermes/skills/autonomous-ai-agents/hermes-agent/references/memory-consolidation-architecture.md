# Memory Consolidation: Architecture & Known Gaps

> Discovered 2026-05-16 during a diagnostic session.

## Overview

Hermes has two parallel memory systems:

| System | Storage | Purpose |
|--------|---------|---------|
| **MEMORY.md / USER.md** | Flat markdown files at `~/.hermes/memories/` | Durable facts, user profile — manually written by the agent via the `memory` tool |
| **SQLite Memory Store** | `~/.hermes/memory_store.db` | Structured, multi-table — STM, LTM, workflows, wiki, chat buffer |

This note covers the **SQLite Memory Store** and its consolidation pipeline.

## Tables

| Table | Rows (2026-05-16) | Purpose |
|-------|--------------------|---------|
| `short_term_entries` | 250 | Session-scoped messages with role, speaker, topics, intent, emotional_tone. `summarized=1` after consolidation. |
| `long_term_entries` | 30 | Persistent facts with category/key/value, confidence, retrieval count. |
| `workflow_entries` | 0 | Procedural patterns with usage-based decay. |
| `wiki_entries` | 2 | Karpathy LLM Wiki knowledge chunks. |
| `consolidation_log` | 0 | Audit trail of STM→LTM/WFM promotions. |
| `chat_message_buffer` | 7166 | Group/DM message history for context retrieval. |

## Consolidation Pipeline

Defined in `agent/memory/consolidation.py` (`MemoryConsolidator` class).

### Triggers
- Only fires when a session has ≥ **6 turns** (`CONSOLIDATION_MIN_TURNS = 6`).
- Turns are tracked per session_id in `short_term_entries.turn_index`.

### Phases (per `consolidate()`)
1. **Extract** — from STM entries: frequent topics (≥2 occurrences), key facts (`I am X`, `my X is Y` patterns), action patterns.
2. **Promote to LTM** — new facts get `confidence=0.4` (`CONSOLIDATION_NEW_FACT_CONFIDENCE`); existing facts get `+0.08` boost (`CONSOLIDATION_EXISTING_BOOST`). Topics become `category="knowledge"` facts at half confidence.
3. **Detect workflows** — repeated action patterns → `workflow_entries` at `base_weight=0.3`.
4. **Apply decay** — `wfm.apply_decay_all()` lowers unused workflow weights.
5. **Mark summarized** — `store.mark_summarized(session_id, max_turn)` sets `summarized=1` on STM.

### Cleanup
- `store.prune_short_term(max_age_days=7.0)` — deletes STM entries older than 7 days.
- `store.trim_chat_buffer(chat_id, keep=200)` — caps per-chat message history.

## 🚨 Known Gap: No Trigger Mechanism

**The consolidation pipeline is fully implemented but never invoked.**

Evidence:
- `consolidation_log` has 0 entries (never ran).
- All 250 STM entries have `summarized=0`.
- `MemoryConsolidator.consolidate()` is not called from anywhere — no session-end hook, no cron job, no auto-trigger in `run_agent.py` or the gateway.
- The `memory_gateway` tool's `consolidate` action (`_handle_consolidate` in `tools/memory_gateway_tool.py`) is a **stub** — it returns a message saying "[c]onsolidation runs at session boundaries" without actually executing the pipeline.

### Workarounds

**Manual trigger (not working currently):**
Call `memory_gateway(action="consolidate")` — but this just returns info text, doesn't execute.

**To actually fix it, you'd need to wire up one of:**

1. **Session end hook** — call `MemoryConsolidator.consolidate()` when a session ends (gateway timeout, `/reset`, CLI exit).
2. **Cron job** — periodic consolidation for all active sessions.
3. **Turn threshold** — auto-trigger after N accumulated turns across sessions.
4. **Fix `_handle_consolidate`** — make the existing tool action actually call the consolidator.

### Quick Test (from Python)

```python
from agent.memory.store import MemoryStore
from agent.memory.short_term import ShortTermMemory
from agent.memory.long_term import LongTermMemory
from agent.memory.workflow import WorkflowMemory
from agent.memory.consolidation import MemoryConsolidator

store = MemoryStore()
stm = ShortTermMemory(store)
ltm = LongTermMemory(store)
wfm = WorkflowMemory(store)
consolidator = MemoryConsolidator(store, stm, ltm, wfm)

# For a specific session:
result = consolidator.consolidate("session_id_here")
print(result)
```

## Files (relative to hermes-agent repo root)

| Path | Role |
|------|------|
| `agent/memory/store.py` | SQLite schema + CRUD (MemoryStore class) |
| `agent/memory/consolidation.py` | Consolidation pipeline (MemoryConsolidator class) |
| `agent/memory/short_term.py` | STM query helpers (ShortTermMemory class) |
| `agent/memory/long_term.py` | LTM CRUD + confidence | 
| `agent/memory/workflow.py` | Workflow CRUD + decay |
| `agent/memory/gateway.py` | UnifiedMemoryGateway — orchestrates all subsystems |
| `tools/memory_gateway_tool.py` | LLM-facing tool definition (`_handle_consolidate` is the stub) |
