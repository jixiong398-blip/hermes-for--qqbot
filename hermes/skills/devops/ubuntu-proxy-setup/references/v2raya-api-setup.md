# v2rayA API-Based Configuration (v2.2.7.5)

## Context

Discovered during 2026-05-13 session. v2rayA was installed via deb package and configured headlessly using its REST API. The web UI is a Vue.js SPA (`/static/js/app.f8a5d29c.js`) that uses axios for API calls.

## API Endpoints

| Endpoint | Method | Purpose | Auth | Request Body |
|----------|--------|---------|------|-------------|
| `/api/account` | POST | **First-time registration** | None | `{"username":"admin","password":"..."}` |
| `/api/login` | POST | Subsequent login | None | `{"username":"admin","password":"..."}` |
| `/api/import` | POST | Import subscription URL | Bearer token | `{"url":"https://..."}` |
| `/api/touch` | GET | Poll state (running, connectedServer) | Bearer token | None (query params) |
| `/api/touch` | GET | With `timeout` param for long-poll | Bearer token | `?timeout=30000` |
| `/api/v2ray` | POST | **Connect node** — adds server to connectedServer | Bearer token | `{"id": <server_id>}` (adds to list, doesn't replace) |
| `/api/v2ray` | POST | **Query state** (no side effects) | Bearer token | `{}` (empty body) |
| `/api/v2ray` | DELETE | **Stop v2ray core** (running=false, service stays up) | Bearer token | None |

## Key Discovery: Registration vs Login

The most critical finding: **v2rayA uses two different endpoints for first-time setup vs subsequent logins.**

- **First time**: `POST /api/account` — creates the admin account, returns a token
- **Subsequent**: `POST /api/login` — authenticates existing account, returns a token

Using `POST /api/login` when no account exists returns:
```json
{"code":"FAIL","data":null,"message":"wrong username or password"}
```

This is NOT a helpful error — it looks like a wrong password but actually means "no account exists yet, use /api/account instead."

## Server Connection Management

### Connecting a Node

Use `POST /api/v2ray` with the server ID:

```bash
curl -s -X POST http://localhost:2017/api/v2ray \
  -H "Authorization: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id":6}'
```

**Important**: This ADDS the server to the existing `connectedServer` list. It does NOT replace the current list. If 5 servers are already connected, adding ID 6 results in 6 connected servers.

### Stopping the Core

```bash
curl -s -X DELETE http://localhost:2017/api/v2ray -H "Authorization: $TOKEN"
```
Returns `running: false`. The v2raya.service itself stays running.

### Querying Without Side Effects

```bash
curl -s -X POST http://localhost:2017/api/v2ray \
  -H "Authorization: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'
```
Same response as `GET /api/touch` but with HTTP POST — no state is modified.

### Limitations

- **No API to disconnect individual servers**: `DELETE /api/v2ray/server/{id}` returns 404. Use the web UI to deselect nodes.
- **No API to toggle autoSelect**: `PUT /api/touch` with `{"id":1,"autoSelect":true}` does not take effect. Use the web UI.
- **connectedServer persists across restart**: After `sudo systemctl restart v2raya`, the same `connectedServer` list is preserved (stored in bolt.db).

### Practical Recommendation

To get a clean connectedServer list with only the servers you want, use the web UI:
1. Visit http://localhost:2017 and log in
2. Go to the node list
3. Deselect unwanted nodes by unchecking them
4. Select only the desired nodes
5. The web UI handles the API communication internally

## Full Setup Script

```bash
# Step 1: Create admin account
curl -s -X POST http://localhost:2017/api/account \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"123456"}'
# -> {"code":"SUCCESS","data":{"token":"eyJ..."},"message":null}

# Step 2: Login to get auth token
TOKEN=$(curl -s -X POST http://localhost:2017/api/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"123456"}' | \
  python3 -c "import json,sys; print(json.load(sys.stdin)['data']['token'])")

# Step 3: Import subscription
curl -s -X POST http://localhost:2017/api/import \
  -H "Content-Type: application/json" \
  -H "Authorization: $TOKEN" \
  -d '{"url":"https://your-subscription-url"}'

# Step 4: Check connection state
curl -s http://localhost:2017/api/touch -H "Authorization: $TOKEN" | python3 -m json.tool
```

## Subscription Import Response Structure

```json
{
  "code": "SUCCESS",
  "data": {
    "running": false,
    "touch": {
      "servers": [],
      "subscriptions": [{
        "id": 1,
        "_type": "subscription",
        "servers": [{"id": 1, "_type": "subscriptionServer", "name": "...", "address": "...", "net": "vmess(ws)", "pingLatency": ""}],
        "autoSelect": false
      }],
      "connectedServer": null
    }
  }
}
```

## Endpoints That Don't Exist (returns 404)

- `PUT /api/connection` -- 404
- `POST /api/register` -- 404
- `/api/v2raya/*` -- 404 (all sub-paths)
- `POST /api/subscription` with URL data -- fails with "bad request: ID exceed range"

## Password Reset

```bash
sudo systemctl stop v2raya
sudo rm -f /etc/v2raya/bolt.db /etc/v2raya/boltv4.db
sudo systemctl start v2raya
# Re-register via POST /api/account
```

## Proxy Port Discovery: 20170 is the SOCKS5 Port

**v2rayA does NOT provide proxy on standard ports (10808/10809).** Instead:
- **SOCKS5**: `127.0.0.1:20170` -- confirmed working for all traffic (HTTP/HTTPS/SOCKS)
- **HTTP**: `127.0.0.1:20171` -- returns HTTP 400 on direct GET
- **Internal API**: `127.0.0.1:20172` -- not a proxy

The GNOME system proxy may be pre-configured for manual v2ray ports (10808/10809). After switching to v2rayA, those ports are dead.

**Fix**: Point GNOME proxy to SOCKS5 :20170 for all protocols:
```bash
gsettings set org.gnome.system.proxy mode 'manual'
gsettings set org.gnome.system.proxy.socks host '127.0.0.1'
gsettings set org.gnome.system.proxy.socks port 20170
gsettings set org.gnome.system.proxy.http host '127.0.0.1'
gsettings set org.gnome.system.proxy.http port 20170
gsettings set org.gnome.system.proxy.https host '127.0.0.1'
gsettings set org.gnome.system.proxy.https port 20170
```

## Settings API (PUT /api/setting)

| Field | Values | Purpose |
|-------|--------|---------|
| `transparent` | `"close"`, `"proxy"`, etc. | **Keep "close" to avoid interfering with local services** |
| `proxyModeWhenSubscribe` | `"direct"`, `"rule"`, `"proxy"` | `"rule"` = geosite-based routing (domestic direct, foreign proxy) |
| `antipollution` | `"closed"`, `"simple"` | DNS anti-pollution. `"simple"` fixes sites that won't resolve |
| `muxOn` | `"yes"`, `"no"` | Multiplexing |
| `subscriptionAutoUpdateMode` | `"none"`, `"auto"` | Auto-refresh subscription |
| `subscriptionAutoUpdateIntervalHour` | int | Refresh interval (e.g. 12) |

Limitation: settings API does NOT support `socksPort`/`httpPort` fields.

## Node Latency Analysis Pattern

```bash
TOKEN=$(cat /tmp/v2raya_token)
curl -s http://localhost:2017/api/touch -H "Authorization: $TOKEN" | python3 -c "
import sys,json
d = json.load(sys.stdin)
sub = d['data']['touch']['subscriptions'][0]
servers = sorted(sub['servers'], key=lambda s: int(s.get('pingLatency','9999').rstrip('ms') or '9999'))
for s in servers:
    lat = s.get('pingLatency','?')
    print(f'ID:{s[\"id\"]:2d}  {lat:>6}  {s[\"name\"]}')
"
```

## Pitfalls

- Registration uses `/api/account`, not `/api/login` -- `/api/login` gives misleading "wrong password" error when no account exists
- `sudo rm` of bolt.db may show "not found" -- use `sudo ls -la` first to confirm root ownership
- v2rayA proxy ports (SOCKS5 :20170) differ from manual v2ray (SOCKS5 :10808, HTTP :10809) -- update GNOME proxy settings after switching
- Pre-existing GNOME proxy config may point to dead ports (10808/10809) after migrating from manual v2ray to v2rayA
