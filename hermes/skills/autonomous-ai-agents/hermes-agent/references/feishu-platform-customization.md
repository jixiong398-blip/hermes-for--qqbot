# Feishu Platform Customization

## Processing Status Reactions

The Feishu adapter uses message reactions (the prominent badge that appears on a message) to indicate processing state. These are defined as constants at the top of `gateway/platforms/feishu.py`.

### Reaction Constants

```python
# Line ~236 in feishu.py
_FEISHU_REACTION_IN_PROGRESS = "Typing"   # Shown while agent is processing
_FEISHU_REACTION_FAILURE = "CrossMark"    # Shown on processing failure
```

### Supported emoji_type Values

| Value | Display | Notes |
|-------|---------|-------|
| `"Typing"` | ⌨️ Keyboard icon | Default, shows while agent is responding |
| `"CrossMark"` | ❌ Cross mark | Shown on failure |
| `"HEART"` | ❤️ Heart | UPPERCASE only! |
| `"heart"` | ❌ (fails) | Lowercase does NOT work |
| `"Heart"` | ❌ (fails) | Title case does NOT work |
| `"❤"` (Unicode) | ❌ (fails) | Direct Unicode character does NOT work |

**Key finding**: The emoji_type value must be **uppercase** (`"HEART"`). Lowercase, title case, and Unicode characters all fail silently — the Reaction API rejects them but the adapter only logs at DEBUG level.

Other possible values (not tested): `"THUMBSUP"`, `"THUMBSDOWN"`, `"SMILE"`, `"CRY"`, `"ANGRY"`, `"LAUGH"`, `"SURPRISED"` (per common Feishu Reaction API patterns).

### Where to Modify

⚠️ **Two files, both must be modified:**

1. **Runtime code** (this is what Gateway actually loads):
   ```
   ~/.hermes/.venv/lib/python3.11/site-packages/gateway/platforms/feishu.py
   ```

2. **Source code** (development mirror):
   ```
   ~/.hermes/gateway/platforms/feishu.py
   ```

The Gateway loads the platform adapter from site-packages (`~/.hermes/.venv/lib/python3.11/site-packages/`), NOT from the source directory (`~/.hermes/gateway/`). Modifying only the source file has no effect on the running Gateway.

### Restart Required

After modifying either file, restart the Gateway:
```bash
pkill -f "hermes gateway run"
hermes gateway run -v --accept-hooks   # in background
```

### Debugging

The `_add_reaction` method logs failures at DEBUG level only (not INFO). To verify whether reactions are being attempted:

1. Add a temporary INFO log at the top of `on_processing_start`:
   ```python
   logger.info("[Feishu] on_processing_start called, reactions=%s, msg_id=%s",
                self._reactions_enabled(), event.message_id)
   ```

2. Check gateway.log:
   ```bash
   grep "on_processing_start\|reaction" ~/.hermes/logs/gateway.log
   ```

### Reaction Lifecycle

1. `on_processing_start()` — adds `_FEISHU_REACTION_IN_PROGRESS` to the user's message
2. `on_processing_complete()` — removes the in-progress reaction on success
3. On FAILURE outcome — adds `_FEISHU_REACTION_FAILURE` (CrossMark)

Reactions are tracked in `_pending_processing_reactions` (OrderedDict, LRU cache of 1024 entries). If a reaction fails to be added, the lifecycle skips removal/fallback to avoid stacking badges.
