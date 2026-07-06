"""
Live2D Debug Tool — MiMo v2.5 vision-powered feedback loop.

Captures screenshots of the Live2D window, sends them to MiMo v2.5
for expression analysis, and reports whether the displayed emotion
matches the expected one.

Usage:
    python -m gateway.live2d_debug snapshot   # Take one screenshot + analyze
    python -m gateway.live2d_debug watch -n 5  # Watch mode, N iterations
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import sys
import time
from pathlib import Path

logger = logging.getLogger("hermes.live2d_debug")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

# ═══════════════════════════════════════════════════════
# Config (from config.yaml)
# ═══════════════════════════════════════════════════════

MIMO_MODEL = "mimo-v2.5"
MIMO_BASE_URL = os.environ.get("MIMO_BASE_URL") or "https://opencode.ai/zen/go/v1/chat/completions"
MIMO_API_KEY = os.environ.get("MIMO_API_KEY", os.environ.get("XIAOMI_API_KEY", os.environ.get("DEEPSEEK_API_KEY", "")))

LIVE2D_URL = "http://127.0.0.1:19919"  # Live2D Electron window
SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "output" / "live2d_debug"


# ═══════════════════════════════════════════════════════
# Screenshot capture
# ═══════════════════════════════════════════════════════

async def capture_screenshot() -> bytes:
    """Capture Live2D window screenshot via Playwright."""
    from playwright.async_api import async_playwright

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page = await browser.new_page(viewport={"width": 600, "height": 800})
        try:
            await page.goto(LIVE2D_URL, timeout=10000)
            await page.wait_for_timeout(2000)  # Let Live2D load
            screenshot = await page.screenshot(type="png", full_page=False)
            ts = time.strftime("%Y%m%d_%H%M%S")
            path = SCREENSHOT_DIR / f"live2d_{ts}.png"
            path.write_bytes(screenshot)
            logger.info(f"Screenshot saved: {path}")
            return screenshot
        finally:
            await browser.close()


# ═══════════════════════════════════════════════════════
# MiMo v2.5 Vision analysis
# ═══════════════════════════════════════════════════════

VISION_PROMPT = """You are debugging a Live2D virtual character named "素世"(Soyo).
Look at this screenshot of the Live2D window and answer:

1. What expression is the character currently showing?
   Options: happy, sad, angry, surprised, thinking, shy, neutral

2. Does the expression look natural and appropriate for a character who is:
   - 17-year-old female high school student
   - Gentle but slightly sarcastic personality
   - Currently in idle/chatting state

3. Any visual issues? (mouth open when should be closed, weird pose, frozen animation)

Reply in JSON:
{"expression": "...", "natural": true/false, "issues": "..."}"""


async def analyze_screenshot(image_bytes: bytes) -> dict:
    """Send screenshot to MiMo v2.5 for expression analysis."""
    import httpx

    img_b64 = base64.b64encode(image_bytes).decode()

    payload = {
        "model": MIMO_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 200,
        "temperature": 0.3,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            MIMO_BASE_URL,
            headers={
                "Authorization": f"Bearer {MIMO_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if resp.status_code != 200:
            logger.error(f"MiMo HTTP {resp.status_code}: {resp.text[:300]}")
            return {"error": f"HTTP {resp.status_code}"}

        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        logger.info(f"MiMo raw: {content[:300]}")

        # Try to parse JSON from response
        try:
            # Extract JSON block if wrapped in markdown
            if "```json" in content:
                start = content.index("```json") + 7
                end = content.index("```", start)
                content = content[start:end].strip()
            elif "```" in content:
                start = content.index("```") + 3
                end = content.index("```", start)
                content = content[start:end].strip()
            return json.loads(content)
        except (json.JSONDecodeError, ValueError):
            return {"raw": content, "expression": "unknown"}


# ═══════════════════════════════════════════════════════
# Emotion verification
# ═══════════════════════════════════════════════════════

EXPECTED_EXPRESSIONS = {
    "happy": ["happy", "smile", "smiling"],
    "sad": ["sad", "frowning", "upset"],
    "angry": ["angry", "annoyed"],
    "surprised": ["surprised", "shocked"],
    "thinking": ["thinking", "pensive", "thoughtful"],
    "neutral": ["neutral", "idle", "default", "calm"],
}


async def verify_emotion(expected: str) -> dict:
    """Capture + analyze, then check if expression matches expected emotion."""
    screenshot = await capture_screenshot()
    result = await analyze_screenshot(screenshot)

    detected = result.get("expression", "unknown").lower()
    expected_set = EXPECTED_EXPRESSIONS.get(expected, [expected])
    match = any(e in detected for e in expected_set)

    report = {
        "expected": expected,
        "detected": detected,
        "match": match,
        "natural": result.get("natural", False),
        "issues": result.get("issues", ""),
        "raw": result.get("raw", ""),
    }
    return report


# ═══════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════

async def cmd_snapshot():
    """Take one screenshot and analyze."""
    print("📸 Capturing Live2D screenshot...")
    screenshot = await capture_screenshot()
    print("🤖 Sending to MiMo v2.5 for analysis...")
    result = await analyze_screenshot(screenshot)
    print(json.dumps(result, ensure_ascii=False, indent=2))


async def cmd_watch(n: int = 5, interval: float = 3.0):
    """Watch mode: take screenshots every N seconds."""
    print(f"👁 Watch mode: {n} iterations, {interval}s interval")
    for i in range(n):
        print(f"\n── Iteration {i+1}/{n} ──")
        screenshot = await capture_screenshot()
        result = await analyze_screenshot(screenshot)
        expr = result.get("expression", "?")
        natural = "✅" if result.get("natural") else "⚠️"
        issues = result.get("issues", "")
        print(f"  Expression: {expr} {natural}")
        if issues:
            print(f"  Issues: {issues}")
        if i < n - 1:
            await asyncio.sleep(interval)


async def cmd_test_emotions():
    """Test all emotions: for each, verify the expression matches."""
    emotions = ["happy", "sad", "angry", "surprised", "thinking", "neutral"]
    results = []
    for emotion in emotions:
        print(f"\n🧪 Testing: {emotion}")
        # Trigger the emotion via Live2D WS
        try:
            import websockets
            async with websockets.connect("ws://127.0.0.1:9190") as ws:
                await ws.send(json.dumps({"type": "expression", "name": f"soyo/{emotion}01"}))
                await asyncio.sleep(1.5)
        except Exception as e:
            print(f"  ⚠️ Could not trigger: {e}")

        report = await verify_emotion(emotion)
        match = "✅" if report["match"] else "❌"
        print(f"  Expected: {emotion} | Detected: {report['detected']} {match}")
        if report["issues"]:
            print(f"  Issues: {report['issues']}")
        results.append(report)

    # Summary
    matched = sum(1 for r in results if r["match"])
    print(f"\n📊 Summary: {matched}/{len(results)} expressions matched")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "snapshot"
    n = int(sys.argv[sys.argv.index("-n") + 1]) if "-n" in sys.argv else 5

    if cmd == "snapshot":
        asyncio.run(cmd_snapshot())
    elif cmd == "watch":
        asyncio.run(cmd_watch(n=n))
    elif cmd == "test":
        asyncio.run(cmd_test_emotions())
    else:
        print(f"Usage: python -m gateway.live2d_debug [snapshot|watch|test]")
