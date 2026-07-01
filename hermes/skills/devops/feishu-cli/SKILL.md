---
name: feishu-cli
description: Install, authenticate, and use the Lark/Feishu CLI (lark-cli) for API access to Feishu spreadsheets, documents, sheets, and other resources
category: devops
---

# Feishu CLI (lark-cli)

Install, authenticate, and use the official Lark/Feishu CLI tool to interact with Feishu Open APIs.

## Installation

### Do NOT use pip
The PyPI package `feishu-cli` (v1.0.3) is **fake** — it only prints a message redirecting to npm. Uninstall it immediately:
```bash
pip uninstall feishu-cli
```

### Install via npm
The official CLI is `@larksuite/cli`, installed globally:
```bash
npm install -g @larksuite/cli
```

The binary is called `lark-cli`.

## Authentication (Device Flow)

### 1. Find credentials
Feishu app credentials are in `~/.hermes/config.yaml` under `platforms.feishu.extra`:
```yaml
platforms:
  feishu:
    enabled: true
    extra:
      app_id: cli_xxxxx
      app_secret: xxxxxx
```

### 2. Add credentials to .env
Add to `~/.hermes/.env`:
```
FEISHU_APP_ID=cli_xxxxx
FEISHU_APP_SECRET=xxxxxx
```

### 3. Bind to Hermes
```bash
lark-cli config bind --source hermes --identity user-default
```
- `bot-only`: safer, no impersonation, can't access user resources
- `user-default`: needed for personal resources (sheets, docs, drive)

### 4. Device flow login
Get a verification URL:
```bash
lark-cli auth login --recommend --no-wait --json
```
Returns JSON with `verification_url` and `device_code`. Show the URL to the user exactly as-is (opaque string, fenced code block).

After user confirms authorization in browser, complete the flow:
```bash
lark-cli auth login --device-code <device_code>
```

**Important**: The URL and device_code expire after 600s. Generate a fresh one each attempt.

### 5. Verify
```bash
lark-cli auth status
```

## Creating a Spreadsheet

### Create
```bash
lark-cli api POST /open-apis/sheets/v3/spreadsheets \
  --data '{"title":"My Title"}' --format json
```
Returns `spreadsheet_token` in response.

### Find sheet ID
```bash
lark-cli api GET "/open-apis/sheets/v3/spreadsheets/{token}/sheets/query" --format json
```
The default first sheet ID is usually a hex string like `"64d1d7"`.

### Write data
```bash
lark-cli api PUT /open-apis/sheets/v2/spreadsheets/{token}/values \
  --data '{
    "valueRange": {
      "range": "{sheet_id}!A1:C{N}",
      "values": [
        ["Header1", "Header2", "Header3"],
        ["Row1Col1", "Row1Col2", 123]
      ]
    }
  }' --format json
```

**Pitfalls:**
- API key is `valueRange` (camelCase), NOT `value_range`
- Range format: `{sheet_id}!A1:C{N}` — sheet_id comes from `sheets/query`, NOT "Sheet1"
- Return values are under `data.spreadsheet` or `data.sheets`

## Checking Scopes
```bash
lark-cli auth scopes
```

## Logout
```bash
lark-cli auth logout
```
