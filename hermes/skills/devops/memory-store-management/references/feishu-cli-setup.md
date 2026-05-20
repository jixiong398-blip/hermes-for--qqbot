# Feishu CLI (lark-cli) Setup

## Install

```bash
# DO NOT install the pip package "feishu-cli" — it's a fake/placeholder
pip uninstall feishu-cli -y --break-system-packages

# Install the real CLI from npm
npm install -g @larksuite/cli
# Command is `lark-cli`
```

## Bind to Hermes

Credentials are in `~/.hermes/config.yaml` under `platforms.feishu.extra`:
- `app_id`
- `app_secret`

Add to `~/.hermes/.env`:
```
FEISHU_APP_ID=<app_id>
FEISHU_APP_SECRET=<app_secret>
```

Then bind:
```bash
lark-cli config bind --source hermes --identity <bot-only|user-default>
```

- `bot-only` (safer default) — no impersonation, cannot access user personal resources
- `user-default` — impersonates user (needed for personal calendar/mail/drive). Ask user to confirm.

## Authorization (user must complete)

```bash
lark-cli auth login --recommend
```
This outputs a verification URL. **Forward it to the user verbatim** (code block, no markdown link wrapping). The user opens it in their own browser to authorize. Command blocks for ~10 min waiting.

If you need async flow (show URL then wait for user to say "done"):
```bash
lark-cli auth login --no-wait --json
# Returns device_code + verification_url
# Show verification_url to user
# After user confirms, run:
lark-cli auth login --device-code <device_code>
```

**Important**: Do not restart the auth flow while waiting — the new device code invalidates the old one, breaking the user's authorization link.

## Usage examples
```bash
lark-cli api GET /open-apis/calendar/v4/calendars
lark-cli calendar +agenda
lark-cli contact +search-user --query "John"
```
