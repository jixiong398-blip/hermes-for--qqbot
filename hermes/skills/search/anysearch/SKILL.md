---
name: anysearch
description: AnySearch — unified real-time search engine. Supports general web search, vertical domain search (23 domains), and URL content extraction.
version: 2.0.0
credentials:
  - name: ANYSEARCH_API_KEY
    required: false
    description: "API key for higher rate limits. Anonymous access available."
    storage: .env
---

# AnySearch Skill

Unified real-time search for AI agents. Two ways to use:

1. **MCP** (recommended): Tools prefixed `mcp_anysearch_*` auto-discovered via Hermes native MCP client
2. **CLI** (fallback): `python ~/.opencode/skills/anysearch/scripts/anysearch_cli.py <command>`

## When to Use

- User asks to search the web, look up facts, news, documentation
- Fact-checking or verifying claims
- Vertical domain queries (code, tech, finance, academic, etc.)
- URL content extraction beyond search snippets

## CLI Commands

| Command | Description |
|---------|-------------|
| `search <query> [options]` | Single query search |
| `batch <query1> \| <query2>` | Parallel batch search |
| `list_domains --domain <name>` | List vertical sub-domains |
| `extract <url>` | Extract full page content |

### search options

| Option | Description |
|--------|-------------|
| `--domain, -d` | Vertical domain filter |
| `--sub_domain, -s` | Sub-domain routing key |
| `--content_types, -t` | web, news, code, doc, academic |
| `--zone, -z` | cn / intl |
| `--max_results, -m` | 1-100, default 10 |
| `--freshness, -f` | day / week / month / year |

### batch options

| Option | Description |
|--------|-------------|
| `--max_results, -m` | Results per query, default 3 |

## Invocation

```bash
python ~/.opencode/skills/anysearch/scripts/anysearch_cli.py search "query" --max_results 5
python ~/.opencode/skills/anysearch/scripts/anysearch_cli.py batch "q1 | q2 | q3" --max_results 3
python ~/.opencode/skills/anysearch/scripts/anysearch_cli.py list_domains --domain tech
python ~/.opencode/skills/anysearch/scripts/anysearch_cli.py extract "https://example.com"
```

## General Web Search (no domain filter)

```
python ~/.opencode/skills/anysearch/scripts/anysearch_cli.py search "what is quantum computing" --max_results 5
```

## Vertical Search

Always call `list_domains` first to discover sub_domains and query formats:

```
python ~/.opencode/skills/anysearch/scripts/anysearch_cli.py list_domains --domain tech
```

Then search with the correct format:

```
python ~/.opencode/skills/anysearch/scripts/anysearch_cli.py search "Go 1.22 release notes" --domain tech --content_types web,doc
```

Available domains: code, tech, fashion, travel, home, ecommerce, gaming, film, music, finance, academic, legal, business, ip, security, education, health, religion, geo, environment, energy, ugc.

## API Key

Configured in `~/.opencode/skills/anysearch/.env` via:
```
ANYSEARCH_API_KEY={{ANYSEARCH_KEY}}
```

## Installation Reference

See `references/installation.md` for the full installation record — MCP configs, skill paths, runtime detection, and verification commands for this environment.
