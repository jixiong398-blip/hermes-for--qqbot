---
name: ubuntu-proxy-setup
description: Install and configure proxy clients (v2ray/xray) on Ubuntu Linux without sudo. Covers subscription parsing, config generation, systemd user service, and GNOME system proxy integration.
category: devops
tags: [proxy, v2ray, xray, ubuntu, linux, systemd-user, gnome, socks5, subscription]
---

# Ubuntu Proxy Setup (No Sudo)

## Triggers
- User wants to install/replace a proxy client (clash → v2ray/xray/others)
- User provides a subscription URL (base64-encoded vmess/vless links)
- System proxy needs to be configured for desktop use (GNOME)
- Clash needs to be replaced/discontinued

## Overview

On Ubuntu (especially managed/locked-down systems), `sudo` requires an interactive terminal. The approach is to install everything user-local and use `systemd --user` services + `gsettings` for system integration.

## Workflow

### 1. Get the Binary

**Preferred**: Download from GitHub releases:
```bash
curl -L -o /tmp/xray.zip "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip"
```

**If download fails** (slow network, proxy issues):
- Ask user to download themselves and send the zip file via Feishu (file gets saved to `~/.hermes/cache/documents/`)
- Copy from cache: `cp ~/.hermes/cache/documents/<filename>.zip /tmp/`

**Alternate**: Send via Feishu document attachment — the file lands at `~/.hermes/cache/documents/doc_<id>_<name>.zip`.

### 2. Extract and Install (No Sudo)

```bash
mkdir -p ~/.local/bin ~/.local/share/v2ray
unzip -o /tmp/xray.zip -d /tmp/v2ray_install/
cp /tmp/v2ray_install/v2ray ~/.local/bin/
chmod 755 ~/.local/bin/v2ray
cp /tmp/v2ray_install/*.dat ~/.local/share/v2ray/
```

The binary goes to `~/.local/bin/` — this should already be in `$PATH` on Ubuntu.

### 3. Parse Subscription

Subscription data is typically base64-encoded, one vmess:// link per line:
```python
import base64, json

# Decode base64
with open("/tmp/sub.txt") as f:
    data = f.read().strip()
decoded = base64.b64decode(data).decode()

# Parse each vmess:// link
for link in decoded.strip().split("\n"):
    if link.startswith("vmess://"):
        b64 = link[8:].split("#")[0]
        padded = b64 + "=" * (4 - len(b64) % 4) if len(b64) % 4 else b64
        info = json.loads(base64.b64decode(padded))
```

**vmess JSON fields**:
| Field | Meaning |
|-------|---------|
| `add` | Server address |
| `port` | Server port |
| `id` | UUID |
| `aid` | AlterID (usually 0) |
| `net` | Network type (tcp/ws/kcp/quic/grpc) |
| `path` | WebSocket path (if net=ws) |
| `host` | Host header (if net=ws) |
| `tls` | TLS setting ("tls" or empty) |
| `scy` | Security method (e.g. "auto", "aes-128-gcm", "chacha20-poly1305", "zero", "2022-blake3-aes-128-gcm") |
| `ps` | Node name/remark |

### 4. Generate v2ray Config

**CRITICAL**: The `outbounds[0].protocol` must ALWAYS be `"vmess"` for vmess:// links — never `"ws"` or the transport protocol name. The transport goes in `streamSettings.network`.

Structure:
```json
{
  "log": {
    "loglevel": "warning",
    "access": "",
    "error": ""
  },
  "inbounds": [
    {
      "tag": "socks-in",
      "port": 10808,
      "listen": "127.0.0.1",
      "protocol": "socks",
      "settings": { "auth": "noauth", "udp": true }
    },
    {
      "tag": "http-in",
      "port": 10809,
      "listen": "127.0.0.1",
      "protocol": "http",
      "settings": {}
    }
  ],
  "outbounds": [
    {
      "tag": "proxy",
      "protocol": "vmess",
      "settings": {
        "vnext": [{
          "address": "<server>",
          "port": <port>,
          "users": [{
            "id": "<uuid>",
            "alterId": 0,
            "security": "auto"
          }]
        }]
      },
      "streamSettings": {
        "network": "<tcp|ws|kcp|quic|grpc>",
        "security": "<tls|none>",
        "wsSettings": {
          "path": "<path>",
          "headers": { "Host": "<host>" }
        },
        "tlsSettings": {
          "serverName": "<host>"
        }
      }
    },
    {
      "tag": "direct",
      "protocol": "freedom"
    }
  ],
  "routing": {
    "domainStrategy": "IPIfNonMatch",
    "rules": [
      { "type": "field", "ip": ["geoip:private", "geoip:cn"], "outboundTag": "direct" },
      { "type": "field", "domain": ["geosite:cn"], "outboundTag": "direct" }
    ]
  }
}
```

Only add `wsSettings` / `tlsSettings` if the node uses those features. For simple TCP nodes, omit them entirely.

Validate config:
```bash
V2RAY_LOCATION_ASSET=~/.local/share/v2ray ~/.local/bin/v2ray test -config <path>
```

### 5. Set Up systemd User Service

```ini
[Unit]
Description=V2Ray Proxy Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=%h/.local/bin/v2ray run -config %h/.config/v2ray/config.json
Environment=V2RAY_LOCATION_ASSET=%h/.local/share/v2ray
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
```

Place at `~/.config/systemd/user/v2ray.service`.

**Commands** (no sudo needed):
```bash
systemctl --user daemon-reload
systemctl --user enable v2ray.service
systemctl --user start v2ray.service
systemctl --user status v2ray.service
```

**Auto-start**: user services start automatically on login (no root needed).

### 6. GNOME System Proxy Integration

```bash
# Enable manual proxy
gsettings set org.gnome.system.proxy mode 'manual'

# SOCKS5
gsettings set org.gnome.system.proxy.socks host '127.0.0.1'
gsettings set org.gnome.system.proxy.socks port 10808

# HTTP/HTTPS
gsettings set org.gnome.system.proxy.http host '127.0.0.1'
gsettings set org.gnome.system.proxy.http port 10809
gsettings set org.gnome.system.proxy.https host '127.0.0.1'
gsettings set org.gnome.system.proxy.https port 10809

# Disable
gsettings set org.gnome.system.proxy mode 'none'
```

This affects all GNOME applications (browsers, settings, software center).

### 7. Proxy Toggle Script

Create `~/.local/bin/proxy`:
```bash
#!/bin/bash
case "$1" in
  on)  systemctl --user start v2ray.service
       gsettings set org.gnome.system.proxy mode 'manual'
       # ... set socks/http/https hosts and ports ...
       echo "✓ Proxy ON (SOCKS5 :10808, HTTP :10809)" ;;
  off) systemctl --user stop v2ray.service
       gsettings set org.gnome.system.proxy mode 'none'
       echo "✗ Proxy OFF" ;;
  status) systemctl --user is-active v2ray.service
          echo "system proxy: $(gsettings get org.gnome.system.proxy mode)"
          ip=$(curl -s --socks5 127.0.0.1:10808 https://api.ip.sb/ip 2>/dev/null)
          echo "Exit IP: ${ip:-fetch failed}" ;;
  restart) systemctl --user restart v2ray.service ;;
  *) echo "Usage: proxy {on|off|status|restart}" ;;
esac
```

### 8. Stopping Clash (Replacement)

After switching to v2ray, stop and disable the old clash service:
```bash
# Check if running
systemctl status clash-verge-service

# Stop (requires sudo — ask user to run)
sudo systemctl stop clash-verge-service
sudo systemctl disable clash-verge-service
```

If `sudo` is unavailable in the agent session, print the commands for the user to run manually.

### 9. Test the Proxy

```bash
# SOCKS5 test
curl -s --socks5 127.0.0.1:10808 https://www.baidu.com -o /dev/null -w "%{http_code}"
curl -s --socks5 127.0.0.1:10808 https://api.ip.sb/ip
```

Expected: `200` for accessible sites, exit IP should NOT be a Chinese IP.

### 10. v2rayA Web Panel Setup

If the user wants a browser-based management panel (node switching, subscription management, traffic stats):

**Approach A — Deb package** (preferred, most reliable on Ubuntu):
1. Download `installer_debian_x64_<version>.deb` from [GitHub releases](https://github.com/v2rayA/v2rayA/releases)
2. If GitHub is unreachable from the server, ask user to download and send via Feishu/QQ — file lands at `~/.hermes/cache/documents/doc_<id>_<name>.deb`
3. Install:
   ```bash
   sudo dpkg -i /path/to/installer_debian_x64_<version>.deb
   ```
4. v2rayA needs to find v2ray binary — create symlink:
   ```bash
   sudo ln -sf ~/.local/bin/v2ray /usr/local/bin/v2ray
   ```
5. Copy geoip/geosite data files to v2rayA config dir so it doesn't need to re-download:
   ```bash
   sudo cp ~/.local/share/v2ray/geoip.dat ~/.local/share/v2ray/geosite.dat /etc/v2raya/
   ```
6. Enable and start:
   ```bash
   sudo systemctl enable v2raya
   sudo systemctl start v2raya
   ```

**Approach B — Snap install** (may work on some systems):
```bash
sudo snap install v2raya
```

**Troubleshooting stuck snap installs**:
```bash
# List snap changes to find stuck installs
snap changes
# Find the change ID for the stuck v2raya install, then:
sudo snap abort <change_id>
```

#### 10.1 First-Time Configuration via API

v2rayA exposes a REST API that can be used to configure it headlessly (no browser needed). The web UI is a Vue.js SPA at `http://localhost:2017`.

**API endpoints discovered** (v2rayA v2.2.7.5):

| Endpoint | Method | Purpose | Request Data |
|----------|--------|---------|-------------|
| `/api/account` | POST | **First-time registration** (NOT `/api/login`!) | `{"username":"admin","password":"..."}` |
| `/api/login` | POST | Subsequent login | Same format, returns token |
| `/api/import` | POST | Import subscription URL | `{"url":"https://..."}` |
| `/api/touch` | GET | Poll current state (running, connected server) | None (needs auth header) |
| `/api/subscription` | PUT | Update existing subscription | `{"id":1,"_type":"SUBSCRIPTION"}` |

**⚠️ Critical distinction**: The Vue.js app uses **`POST /api/account`** for first-time setup and **`POST /api/login`** for subsequent logins. They have the same request body format `{"username":"...","password":"..."}`. Using `/api/login` when no account exists returns `FAIL: wrong username or password` — it does NOT auto-create the account.

**Full setup workflow**:
```bash
# Step 1: Create admin account (first time only)
curl -s -X POST http://localhost:2017/api/account \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"123456"}'
# Returns: {"code":"SUCCESS","data":{"token":"eyJ..."},"message":null}

# Step 2: Login to get token
TOKEN=$(curl -s -X POST http://localhost:2017/api/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"123456"}' | python3 -c "import json,sys; print(json.load(sys.stdin)['data']['token'])")

# Step 3: Import subscription
curl -s -X POST http://localhost:2017/api/import \
  -H "Content-Type: application/json" \
  -H "Authorization: $TOKEN" \
  -d '{"url":"https://your-subscription-url"}'

# Step 4: Check status
curl -s http://localhost:2017/api/touch -H "Authorization: $TOKEN" | python3 -m json.tool
```

**Connecting to a node via API**: Use `POST /api/v2ray` with `{"id": <server_id>}`:
```bash
curl -s -X POST http://localhost:2017/api/v2ray \
  -H "Authorization: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id":6}'
```
Returns full state with the server added to `connectedServer`. Note that this ADDS to the existing connected list — it does NOT replace it.

**Stopping the v2ray core via API**: `DELETE /api/v2ray`:
```bash
curl -s -X DELETE http://localhost:2017/api/v2ray -H "Authorization: $TOKEN"
```
Returns `running: false`. The v2raya.service itself stays running; only the core stops.

**Querying state** (without adding/changing): `POST /api/v2ray` with empty body `{}` — same response as GET /api/touch without side effects.

**Limitations discovered**:
- No API endpoint exists to REMOVE individual servers from connectedServer (`DELETE /api/v2ray/server/{id}` returns 404)
- `PUT /api/touch` with `{"id":1,"autoSelect":true}` does not seem to take effect (autoSelect stays false) — try the web UI for this
- The `connectedServer` list survives v2raya.service restart — it's persisted in bolt.db at `/etc/v2raya/`

Since you can't remove individual servers via API, it's best to do the initial selection through the web UI, or include the desired server IDs from first connection to avoid accumulating unwanted servers.

##### Proxy Port Discovery: v2rayA Uses Non-Standard Ports, Browser Proxy Gotcha

v2rayA does NOT listen on the standard SOCKS5 (10808) or HTTP (10809) proxy ports. Instead:
- **SOCKS5 proxy**: `127.0.0.1:20170` ✅ (confirmed working for all traffic types)
- **HTTP proxy**: `127.0.0.1:20171` (exists but not needed — SOCKS5 handles everything)

This is a **critical difference from manual v2ray setups**. The GNOME system proxy must be reconfigured.

**⚠️ Important: DO NOT set HTTP/HTTPS proxy to the SOCKS5 port**

Setting `gsettings` HTTP/HTTPS proxy to `127.0.0.1:20170` will cause browsers to show **"建立安全连接失败" / "failed to establish secure connection"** for HTTPS sites. This happens because the browser uses HTTP CONNECT on the SOCKS5 port, which the SOCKS5 listener can't handle properly.

**Correct GNOME proxy setup for v2rayA**:
```bash
# ONLY configure SOCKS5 — leave HTTP/HTTPS empty
gsettings set org.gnome.system.proxy mode 'manual'
gsettings set org.gnome.system.proxy.socks host '127.0.0.1'
gsettings set org.gnome.system.proxy.socks port 20170
gsettings set org.gnome.system.proxy.http host ''
gsettings set org.gnome.system.proxy.http port 0
gsettings set org.gnome.system.proxy.https host ''
gsettings set org.gnome.system.proxy.https port 0
```

Most modern Linux browsers (Chrome, Chromium, Firefox) respect the SOCKS5 system proxy for all traffic types — no need for separate HTTP/HTTPS proxy entries.

**Troubleshooting**:
- If a browser still can't connect, check whether it uses system proxy or its own proxy settings
- Some Gnome Web / Epiphany browsers don't support SOCKS5 system proxy well — use a browser extension (SwitchyOmega) with manual SOCKS5 `127.0.0.1:20170` config
- Verify proxy is working: `curl -s --socks5 127.0.0.1:20170 -o /dev/null -w "%{http_code}" https://github.com --connect-timeout 10 --max-time 15` (expect 200)

**Common failure scenario**: System was pre-configured for manual v2ray (ports 10808/10809). After switching to v2rayA, those ports are dead. GitHub etc. fail silently because the browser tries dead proxy ports, OR the user sets HTTP/HTTPS to :20170 and gets TLS errors. Fix by clearing HTTP/HTTPS and using SOCKS5 :20170 only.

#### 10.2 v2rayA Settings API

v2rayA exposes a `PUT /api/setting` endpoint for modifying runtime settings without the web UI:

```bash
TOKEN=$(cat /tmp/v2raya_token)
curl -s -X PUT http://localhost:2017/api/setting \
  -H "Authorization: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "pacMode": "whitelist",
    "proxyModeWhenSubscribe": "rule",
    "subscriptionAutoUpdateMode": "auto",
    "subscriptionAutoUpdateIntervalHour": 12,
    "muxOn": "yes",
    "mux": 8,
    "antipollution": "simple",
    "transparent": "close",
    "ipforward": true,
    "routeOnly": false,
    "portSharing": false,
    "specialMode": "none",
    "transparentType": "redirect",
    "tcpFastOpen": "default",
    "inboundSniffing": "http,tls"
  }'
```

Key setting recommendations for "don't interfere with local services":
- `transparent: "close"` — Transparent proxy intercepts all traffic; keep OFF to avoid breaking local services
- `proxyModeWhenSubscribe: "rule"` — Routes traffic based on geoip/geosite rules (domestic direct, foreign proxy)
- `antipollution: "simple"` — DNS anti-pollution, fixes sites that won't resolve
- `muxOn: "yes"` — Multiplexing, reduces connection overhead
- `subscriptionAutoUpdateMode: "auto"` with `intervalHour: 12` — Keeps node list fresh

**Returned fields from GET /api/setting**: `pacMode`, `proxyModeWhenSubscribe`, `pacAutoUpdateMode`, `pacAutoUpdateIntervalHour`, `subscriptionAutoUpdateMode`, `subscriptionAutoUpdateIntervalHour`, `tcpFastOpen`, `muxOn`, `mux`, `inboundSniffing`, `transparent`, `ipforward`, `routeOnly`, `portSharing`, `specialMode`, `transparentType`, `antipollution`.

Settings API does NOT expose socksPort/httpPort — those appear to be managed through the bolt.db directly.

#### 10.3 v2rayA Data &amp; Reset

v2rayA stores its database at `/etc/v2raya/`:
- `bolt.db` / `boltv4.db` — BoltDB databases containing accounts, settings, subscriptions
- `config.json` — Current proxy routes (auto-generated)
- `geoip.dat` / `geosite.dat` — Geo IP databases

**To reset the admin password**: Delete the bolt DB files and restart:
```bash
sudo systemctl stop v2raya
sudo rm -f /etc/v2raya/bolt.db /etc/v2raya/boltv4.db
sudo systemctl start v2raya
# Then re-register via POST /api/account
```

The `geoip.dat` and `geosite.dat` files persist across resets — no need to re-copy.

#### 10.3 Proxy Toggle Script for v2rayA

When using v2rayA, the proxy toggle script differs from the manual v2ray version because v2rayA listens on different ports:
- SOCKS5: `127.0.0.1:20170`
- HTTP: `127.0.0.1:20171`

```bash
#!/bin/bash
# ~/.local/bin/proxy — v2rayA version

V2RAYA_URL="http://localhost:2017"
V2RAYA_USER="admin"
V2RAYA_PASS="123456"

get_token() {
    curl -s -X POST "$V2RAYA_URL/api/login" \
        -H "Content-Type: application/json" \
        -d "{\"username\":\"$V2RAYA_USER\",\"password\":\"$V2RAYA_PASS\"}" |
        python3 -c "import json,sys; print(json.load(sys.stdin).get('data',{}).get('token',''))"
}

case "$1" in
  on)
    systemctl is-active v2raya > /dev/null 2>&1 || sudo systemctl start v2raya
    gsettings set org.gnome.system.proxy mode 'manual'
    # ⚠️ Only set SOCKS5 — DO NOT set HTTP/HTTPS, causes browser TLS errors
    gsettings set org.gnome.system.proxy.http host ''
    gsettings set org.gnome.system.proxy.http port 0
    gsettings set org.gnome.system.proxy.https host ''
    gsettings set org.gnome.system.proxy.https port 0
    gsettings set org.gnome.system.proxy.socks host '127.0.0.1'
    gsettings set org.gnome.system.proxy.socks port 20170
    echo "✓ Proxy ON (v2rayA)"
    echo "  Panel: http://localhost:2017"
    echo "  Login: admin / [saved password]"
    ;;
  off)
    gsettings set org.gnome.system.proxy mode 'none'
    echo "✗ Proxy OFF"
    ;;
  status)
    echo "v2rayA: $(systemctl is-active v2raya)"
    echo "System proxy: $(gsettings get org.gnome.system.proxy mode)"
    TOKEN=$(get_token)
    [ -n "$TOKEN" ] && curl -s "$V2RAYA_URL/api/touch" -H "Authorization: $TOKEN" |
        python3 -c "
import json,sys
d=json.load(sys.stdin).get('data',{})
print('Running:', d.get('running'))
cs=d.get('touch',{}).get('connectedServer',{})
if cs: print(f'Connected: {cs.get(\"name\")} ({cs.get(\"address\")})')
" 2>/dev/null
    ;;
  web)
    xdg-open http://localhost:2017 2>/dev/null || echo "Open http://localhost:2017 in browser"
    ;;
  *) echo "Usage: proxy {on|off|status|web}" ;;
esac
```

#### 10.4 Web UI Details

The v2rayA web panel is served at `http://localhost:2017` and provides:
- Node list with ping latency
- Connect/disconnect per node
- Subscription update
- Traffic statistics
- DNS & routing settings
- Transparent proxy configuration

The default first-time page shows a registration form (create admin account). After login, the main page shows server list and connection status.

See `references/v2raya-api-setup.md` for the full API reference, endpoint documentation, and session-specific details including the registration-vs-login endpoint distinction and subscription import response structure.

#### 10.5 Desktop Shortcut

See `references/desktop-shortcut.md` for creating a clickable desktop icon to open the v2rayA panel.

#### 10.6 Known Issues

- v2rayA conflicts with manually-managed v2ray configs (stored at `~/.config/v2ray/config.json`). When running v2rayA, it manages its own config database at `/etc/v2raya/`. Don't run both simultaneously.
- The deb package installs v2raya.service as `User=root` — this is safe as it manages privileged ports (80, 443 for transparent proxy).
- After installing, v2rayA may initially fail to start if it can't find geoip.dat. Provide the files by copying from `~/.local/share/v2ray/` as shown in step 5 above. Without them, the web UI shows "Downloading missing geoip.dat and geosite.dat; refresh the page later."

## Replacing Clash

When switching from clash to v2ray:

1. Identify clash processes:
   ```bash
   ps aux | grep -i clash | grep -v grep
   systemctl status clash-verge-service 2>/dev/null
   ```

2. Stop and disable (needs sudo — ask user or use piped password if user explicitly provides it):
   ```bash
   sudo systemctl stop clash-verge-service
   sudo systemctl disable clash-verge-service
   ```

3. If user provides their sudo password explicitly (e.g. "password is 123"), use the pipe pattern:
   ```bash
   echo <password> | sudo -S systemctl stop clash-verge-service
   ```

   ⚠️ Never ask for the user's password. Only use this pattern if the user volunteers it first.

## Pitfalls

- ❗ **protocol must be "vmess" not transport type** — `outbounds[0].protocol` should always be `"vmess"` for vmess:// links. Setting it to `"ws"` errors with `unknown config id: ws`.
- ❗ **Log paths need user-writable dirs** — Default configs use `/var/log/v2ray/` which requires sudo. Set `access` and `error` to empty strings `""` or use `~/.local/share/v2ray/log/`.
- ❗ **sudo not available interactively** — The agent's terminal tool cannot show password prompts. Install everything to `~/.local/`. For system-wide operations (stopping clash, binding privileged ports), print instructions for the user.
- ❗ **`gsettings` only works in GNOME session** — If the user is on a non-GNOME desktop (KDE, Wayland+Sway, CLI-only), gsettings proxy settings do nothing. Check with `echo $XDG_CURRENT_DESKTOP`.
- ❗ **Node selection** — Pick a non-China node (check `add` field doesn't contain `.cn`). If all nodes are Chinese, pick the first one anyway — the routing rules will still send CN traffic direct.
- ❗ **Subscription expiry** — The subscription data may contain traffic limits (shown in `ps` field like `剩余流量：70.65 GB`). Warn user if quota is low.
- ❗ **v2ray vs xray**: v2ray 5.x uses `v2ray run` not `v2ray -config`. Check version with `v2ray version` (not `--version` which is not a valid flag).
- ❗ **Some vmess nodes may report `scy: "zero"`** — this means zero encryption (disabled security). v2ray supports this, but it's less common.
