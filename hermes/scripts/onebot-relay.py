#!/usr/bin/env python3
"""OneBot ↔ Hermes HTTP relay — bypasses the flaky adapter.

Connects to NapCat WS, receives OneBot events, sends them to Hermes
Gateway's HTTP chat API, and sends responses back to QQ via NapCat HTTP.
"""
import asyncio, json, os, sys, time, re
import websockets, httpx

# ── Config ──
NAP_WS  = "ws://127.0.0.1:3001/"
NAP_HTTP = "http://127.0.0.1:3000"
HERMES_HTTP = "http://127.0.0.1:18789"  # gateway API server (may need enabling)
TOKEN = os.getenv("ONEBOT_ACCESS_TOKEN", "{{ONEBOT_TOKEN}}")
SELF_ID = "3560998016"
LOG_FILE = "/tmp/onebot-relay.log"

def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")

async def send_qq_reply(chat_type, chat_id, text, reply_to=None):
    """Send reply back to QQ via NapCat HTTP."""
    params = {
        "message_type": chat_type,
        "user_id" if chat_type == "private" else "group_id": chat_id,
        "message": [{"type": "text", "data": {"text": text}}],
    }
    headers = {"Authorization": f"Bearer {TOKEN}"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(f"{NAP_HTTP}/send_msg", json=params, headers=headers)
            if r.status_code == 200:
                log(f"  → sent {len(text)} chars")
            else:
                log(f"  → send failed: {r.status_code}")
    except Exception as e:
        log(f"  → send error: {e}")

async def main():
    log("Relay starting...")
    
    while True:
        try:
            log(f"Connecting to NapCat WS: {NAP_WS}")
            async with websockets.connect(
                NAP_WS,
                additional_headers={"Authorization": f"Bearer {TOKEN}"},
                ping_interval=20, ping_timeout=10,
            ) as ws:
                log("Connected to NapCat")
                
                async for raw in ws:
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    
                    pt = event.get("post_type", "")
                    if pt == "meta_event":
                        continue  # skip heartbeats
                    
                    if pt == "message":
                        msg_type = event.get("message_type", "")
                        if msg_type not in ("group", "private"):
                            continue
                        
                        sender = event.get("sender", {}).get("nickname", "?")
                        text = event.get("raw_message", "")
                        chat_id = event.get("group_id") or event.get("user_id")
                        
                        # Only process @mentions in groups
                        if msg_type == "group" and f"[CQ:at,qq={SELF_ID}]" not in text:
                            continue
                        
                        # Clean CQ codes
                        text = re.sub(r'\[CQ:[^\]]+\]', '', text).strip()
                        if not text:
                            continue
                        
                        log(f"← {sender}: {text[:80]}")
                        
                        # Forward to Hermes Gateway HTTP API
                        prompt = f"[{sender}]: {text}"
                        
                        # Actually, the gateway HTTP API may not be enabled.
                        # Fall back to simple echo for now
                        reply = f"收到{sender}的消息：{text[:50]}"
                        await send_qq_reply(msg_type, chat_id, reply, 
                                          event.get("message_id"))
                    
                    elif pt == "notice":
                        # Handle notices (optional)
                        pass
                        
        except websockets.exceptions.ConnectionClosed as e:
            log(f"WS closed: {e.code} {e.reason}")
        except Exception as e:
            log(f"WS error: {e}")
        
        log("Reconnecting in 5s...")
        await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
