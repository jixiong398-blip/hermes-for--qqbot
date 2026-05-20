---
name: browser-automation-setup
description: Install and configure agent-browser CLI + Chrome for Testing on Linux (Ubuntu) for Hermes browser toolset
category: devops
---

# Browser Automation Setup

Install and configure the `agent-browser` CLI tool and Chrome for Testing so Hermes' `browser` toolset works on Linux.

## Prerequisites

- Node.js (v18+) and npm — check with `node --version`
- A working SOCKS5 proxy for downloading Chrome (the zip is ~175MB from Google's CDN)
- `curl` with `--socks5` support

## Installation Steps

### 1. Install agent-browser CLI

```bash
npm install -g agent-browser
```

### 2. Download Chrome for Testing

The `agent-browser install` command tries to download Chrome directly, but on modern Ubuntu (23.10+) with AppArmor restrictions or behind a SOCKS5 proxy, it may fail. Download manually:

```bash
# Use SOCKS5 proxy
curl -L --socks5 127.0.0.1:20170 \
  -o /tmp/chrome-linux64.zip \
  "https://storage.googleapis.com/chrome-for-testing-public/148.0.7778.167/linux64/chrome-linux64.zip" \
  --connect-timeout 10 --max-time 600
```

> **Tip**: Check the latest Chrome for Testing version at https://googlechromelabs.github.io/chrome-for-testing/ if 148.0.7778.167 is outdated.

### 3. Extract and place in agent-browser cache

```bash
cd /tmp && unzip -q chrome-linux64.zip
mkdir -p ~/.agent-browser/browsers/chrome-linux64
cp -r /tmp/chrome-linux64/* ~/.agent-browser/browsers/chrome-linux64/
```

Verify:
```bash
~/.agent-browser/browsers/chrome-linux64/chrome --version
# Expected: Google Chrome for Testing 148.0.7778.167
```

### 4. Handle the sandbox issue

Ubuntu 23.10+ disables unprivileged user namespaces via AppArmor. Chrome needs `--no-sandbox`:

```bash
# Set permanently in shell
echo 'export AGENT_BROWSER_ARGS="--no-sandbox,--disable-gpu"' >> ~/.bashrc

# Also for systemd/gateway services
mkdir -p ~/.config/environment.d
echo 'AGENT_BROWSER_ARGS=--no-sandbox,--disable-gpu' > ~/.config/environment.d/agent-browser.conf
```

### 5. Test

```bash
AGENT_BROWSER_ARGS="--no-sandbox,--disable-gpu" \
  agent-browser open https://example.com --headless --json \
  --executable-path ~/.agent-browser/browsers/chrome-linux64/chrome
```

Expected output: `{"success":true,"data":{"title":"Example Domain","url":"https://example.com/"}}`

## Testing GitHub Access

```bash
AGENT_BROWSER_ARGS="--no-sandbox,--disable-gpu" \
  agent-browser open https://github.com --headless --json \
  --executable-path ~/.agent-browser/browsers/chrome-linux64/chrome
```

## Pitfalls

- **agent-browser is an npm global binary**, not a Python package. Don't try `pip install agent-browser` (it doesn't exist on PyPI).
- **Playwright does NOT support Ubuntu 26.04** (Resolute Raccoon) as of mid-2026. The `playwright install firefox` / `playwright install chromium` commands will refuse with "does not support ubuntu26.04-x64". Chrome for Testing works fine when manually deployed.
- **The `--args` flag is comma-separated**: `--args "--no-sandbox,--disable-gpu"` — space-separated won't work.
- **System Firefox cannot be used with Playwright** — Playwright requires its own patched Firefox build. The system `firefox --version` is irrelevant.
- **agent-browser's `install` command doesn't support HTTP_PROXY env vars with SOCKS5** (it uses Node.js HTTP which only handles HTTP CONNECT proxies). Use `curl --socks5` as a workaround.
- **BROWSERBASE / cloud mode**: The Hermes browser_tool.py supports cloud providers (Browserbase, Browser Use) when credentials are set. Local mode is the default and works without any cloud API key.

## Hermes Integration

The `browser` toolset is in `_HERMES_CORE_TOOLS` and includes:
`browser_navigate`, `browser_snapshot`, `browser_click`, `browser_type`, `browser_scroll`, `browser_back`, `browser_press`, `browser_get_images`, `browser_vision`, `browser_console`, `browser_cdp`, `browser_dialog`

For the OneBot platform, these are **excluded** from `_HERMES_ONEBOT_TOOLS` (role-layer restriction). To use browser tools on OneBot, enable a different toolset or use the CLI/Feishu platform.
