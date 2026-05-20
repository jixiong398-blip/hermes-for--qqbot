# OpenCode Local Configuration (清尘's Server)

## Version & Installation
- **opencode**: 1.14.46 at `/home/ji/.local/bin/opencode`
- **oh-my-openagent**: 4.0.0 plugin

## Provider
DeepSeek API: `https://api.deepseek.com/v1` with `@ai-sdk/openai-compatible` npm adapter.

Models available:
- `deepseek-v4-flash` (fast/cheap)
- `deepseek-v4-pro` (full power)

## oh-my-openagent Agents

| Agent | Model | Fallback |
|-------|-------|----------|
| sisyphus | deepseek-v4-pro | deepseek-v4-flash |
| hephaestus | deepseek-v4-pro | deepseek-v4-flash |
| oracle | deepseek-v4-pro | deepseek-v4-flash |
| prometheus | deepseek-v4-pro | deepseek-v4-flash |
| metis | deepseek-v4-pro | deepseek-v4-flash |
| momus | deepseek-v4-pro | deepseek-v4-flash |
| sisyphus-junior | deepseek-v4-flash | — |
| explore | deepseek-v4-flash | — |
| librarian | deepseek-v4-flash | — |
| atlas | deepseek-v4-flash | — |

## Categories

| Category | Model |
|----------|-------|
| ultrabrain | deepseek-v4-pro |
| deep | deepseek-v4-pro |
| visual-engineering | deepseek-v4-pro |
| quick | deepseek-v4-flash |
| writing | deepseek-v4-flash |

## No Custom UI Project

OpenCode is a standard CLI/TUI tool. There is no separate custom UI/dashboard project for opencode on this server. The TUI is built with Ink (React for terminals).

## Config Paths
- `/home/ji/.config/opencode/opencode.json` — main config (provider, plugins)
- `/home/ji/.config/opencode/oh-my-openagent.json` — agent/category definitions
