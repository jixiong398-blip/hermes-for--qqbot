# AnySearch Installation (2026-05-20)

## Environment Summary

- **Server**: Ubuntu 26.04 Linux, bare metal (no Docker)
- **Hermes Home**: `~/.hermes/`
- **Python**: 3.11 (Hermes venv at `~/.hermes/.venv/`)
- **MCP package**: `pip install mcp` in `~/.hermes/.venv/` (via ensurepip + pip)

## What Was Installed

### 1. OpenCode MCP (Streamable HTTP)

**Config file**: `~/.opencode/config.json`

```json
{
  "mcp": {
    "anysearch": {
      "type": "remote",
      "url": "https://api.anysearch.com/mcp",
      "headers": {
        "Authorization": "Bearer {{ANYSEARCH_KEY}}"
      }
    }
  }
}
```

No OpenCode restart needed — config is read on next agent spawn.

### 2. OpenCode SKILL

**Location**: `~/.opencode/skills/anysearch/`
**Source**: https://github.com/anysearch-ai/anysearch-skill/archive/refs/heads/main.zip
**Runtime**: Python (detected via `python3 --version`)
**runtime.conf**: 
```
Runtime: Python
Command: python ~/.opencode/skills/anysearch/scripts/anysearch_cli.py
```
**API Key**: in `~/.opencode/skills/anysearch/.env`

Verified with:
```bash
python ~/.opencode/skills/anysearch/scripts/anysearch_cli.py doc     # OK
python ~/.opencode/skills/anysearch/scripts/anysearch_cli.py search "hello world" --max_results 1  # OK
```

### 3. Hermes MCP (Native HTTP)

**Config file**: `~/.hermes/config.yaml`

```yaml
mcp_servers:
  anysearch:
    url: "https://api.anysearch.com/mcp"
    headers:
      Authorization: "Bearer {{ANYSEARCH_KEY}}"
```

Tools will auto-discover as `mcp_anysearch_*` on next Gateway restart.

### 4. Hermes SKILL (Hermes skill system)

**Location**: `~/.hermes/skills/search/anysearch/`
**Trigger**: User asks to search web, look up facts, news, documentation
**Usage**: Either through MCP tools (`mcp_anysearch_*`) or CLI fallback

## API Key

- **Key**: `{{ANYSEARCH_KEY}}`
- **Scope**: OpenCode SKILL .env + Hermes MCP headers in config.yaml
- **Type**: Authenticated (not anonymous — has higher rate limits)
