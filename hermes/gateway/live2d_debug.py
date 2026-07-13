"""
Live2D Debug Tool - MiMo v2.5 vision-powered feedback loop.

Captures screenshots of the Live2D window via HTTP /screenshot endpoint,
sends them to MiMo v2.5 for expression analysis, and reports whether
the displayed emotion matches the expected one.

Usage:
    python -m gateway.live2d_debug snapshot              # Take one screenshot + analyze
    python -m gateway.live2d_debug watch -n 5            # Watch mode, N iterations
    python -m gateway.live2d_debug test                  # Test all emotions
    python -m gateway.live2d_debug test-char mutsumi     # Test specific character
    python -m gateway.live2d_debug switch mutsumi casual_spring_01  # Switch model
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
# Config
# ═══════════════════════════════════════════════════════

MIMO_MODEL = os.environ.get("LIVE2D_DEBUG_MODEL", "mimo-v2.5")
MIMO_BASE_URL = os.environ.get("MIMO_BASE_URL", "https://opencode.ai/zen/go/v1/chat/completions")
MIMO_API_KEY = os.environ.get("MIMO_API_KEY", os.environ.get("XIAOMI_API_KEY", os.environ.get("DEEPSEEK_API_KEY", "")))

LIVE2D_URL = "http://127.0.0.1:19919"
SCREENSHOT_DIR = Path(__file__).resolve().parent.parent / "output" / "live2d_debug"

# ═══════════════════════════════════════════════════════
# Our Notes Expression Map (emotion -> expression names)
# ═══════════════════════════════════════════════════════

EMOTION_EXPRESSIONS = {
    "happy":     ["exp_smile01", "exp_smile02", "exp_smile03", "exp_bsmile01", "exp_bsmile02"],
    "sad":       ["exp_sad01", "exp_sad02", "exp_sad03", "exp_pale01"],
    "angry":     ["exp_angry01", "exp_upset01", "exp_upset02"],
    "surprised": ["exp_surprised01", "exp_surprised02"],
    "thinking":  ["exp_idle02", "exp_shadow01", "exp_serious01"],
    "shy":       ["exp_shy01"],
    "cry":       ["exp_cry01", "exp_cry02", "exp_dispair01"],
    "serious":   ["exp_serious01", "exp_kime01", "exp_shadow01"],
    "nervous":   ["exp_dispair01", "exp_pale01"],
    "neutral":   ["exp_idle01", "exp_idle02", "exp_idle03", "exp_idle04"],
}

# Character display names (for MiMo prompt)
CHARACTER_NAMES = {
    "mutsumi": "若葉睦 (Mutsumi) - 银发绿眼，Ave Mujica吉他手",
    "taki": "椎名立希 (Taki) - 蓝发，MyGO!!!!!鼓手",
    "tomori": "高松灯 (Tomori) - 短发，MyGO!!!!!主唱",
    "umiri": "八幡海鈴 (Umiri) - 长发，Ave Mujica贝斯手",
    "rana": "海邊ナナ (Rana) - 粉发",
    "yachiyo": "八千代辉夜姬 (Yachiyo)",
}

# ═══════════════════════════════════════════════════════
# Screenshot capture (via HTTP /screenshot endpoint)
# ═══════════════════════════════════════════════════════

async def capture_screenshot() -> bytes:
    """Capture Live2D window screenshot via HTTP /screenshot endpoint."""
    import httpx

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(f"{LIVE2D_URL}/screenshot")
            if resp.status_code != 200:
                logger.error(f"Screenshot failed: HTTP {resp.status_code}")
                return b""

            screenshot = resp.content
            ts = time.strftime("%Y%m%d_%H%M%S")
            path = SCREENSHOT_DIR / f"live2d_{ts}.png"
            path.write_bytes(screenshot)
            logger.info(f"Screenshot saved: {path}")
            return screenshot
        except Exception as e:
            logger.error(f"Screenshot error: {e}")
            return b""


# ═══════════════════════════════════════════════════════
# Send command to Live2D
# ═══════════════════════════════════════════════════════

async def send_command(cmd: dict) -> bool:
    """Send a command to the Live2D window via HTTP /cmd."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{LIVE2D_URL}/cmd",
                json=cmd,
                headers={"Content-Type": "application/json"},
            )
            return resp.status_code == 200
    except Exception as e:
        logger.error(f"Command send error: {e}")
        return False


async def switch_model(character: str, costume: str = "casual_spring_01"):
    """Switch the Live2D model."""
    print(f"🔄 Switching to {character}/{costume}...")
    await send_command({"type": "switch_model", "character": character, "costume": costume})
    await asyncio.sleep(3)  # Wait for model to load
    print(f"   ✅ Switched")


async def trigger_expression(expression_name: str):
    """Trigger a specific expression."""
    await send_command({"type": "expression", "name": expression_name})
    await asyncio.sleep(1.5)  # Wait for expression to apply


async def trigger_motion(motion_name: str):
    """Trigger a specific motion."""
    await send_command({"type": "motion", "name": motion_name})
    await asyncio.sleep(2.0)  # Wait for motion to play


async def trigger_emotion(emotion: str):
    """Trigger an emotion (maps to expression + motion via renderer's EMOTION_MAP)."""
    await send_command({"type": "emotion", "emotion": emotion})
    await asyncio.sleep(1.5)


# ═══════════════════════════════════════════════════════
# MiMo v2.5 Vision analysis
# ═══════════════════════════════════════════════════════

async def analyze_screenshot(image_bytes: bytes, character: str = "mutsumi") -> dict:
    """Send screenshot to MiMo v2.5 for expression analysis."""
    import httpx

    if not MIMO_API_KEY:
        logger.error("No API key found. Set MIMO_API_KEY or XIAOMI_API_KEY or DEEPSEEK_API_KEY")
        return {"error": "no_api_key"}

    if not image_bytes:
        return {"error": "empty_screenshot"}

    char_desc = CHARACTER_NAMES.get(character, f"Live2D character: {character}")

    prompt = f"""You are debugging a Live2D virtual character: {char_desc}
Look at this screenshot of the Live2D window and answer:

1. What expression is the character currently showing?
   Options: happy, sad, angry, surprised, thinking, shy, cry, serious, nervous, neutral

2. Does the expression look natural and appropriate?

3. Any visual issues? (mouth open when should be closed, weird pose, frozen animation, blank/missing model)

Reply in JSON:
{{"expression": "...", "natural": true/false, "issues": "..."}}"""

    img_b64 = base64.b64encode(image_bytes).decode()

    payload = {
        "model": MIMO_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
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
    "happy": ["happy", "smile", "smiling", "cheerful"],
    "sad": ["sad", "frowning", "upset", "melancholy"],
    "angry": ["angry", "annoyed", "frustrated"],
    "surprised": ["surprised", "shocked", "astonished"],
    "thinking": ["thinking", "pensive", "thoughtful", "serious"],
    "shy": ["shy", "embarrassed", "blushing"],
    "cry": ["cry", "crying", "tears", "sobbing"],
    "serious": ["serious", "determined", "focused"],
    "nervous": ["nervous", "anxious", "worried"],
    "neutral": ["neutral", "idle", "default", "calm", "relaxed"],
}


async def verify_emotion(expected: str, character: str = "mutsumi") -> dict:
    """Trigger emotion + capture + analyze + check match."""
    # Trigger the emotion
    expressions = EMOTION_EXPRESSIONS.get(expected, ["exp_idle01"])
    expr = expressions[0]
    print(f"  📤 Triggering: {expected} -> {expr}")
    await trigger_expression(expr)

    # Capture and analyze
    screenshot = await capture_screenshot()
    if not screenshot:
        return {"expected": expected, "detected": "error", "match": False, "issues": "screenshot failed"}

    result = await analyze_screenshot(screenshot, character)

    detected = result.get("expression", "unknown").lower()
    expected_set = EXPECTED_EXPRESSIONS.get(expected, [expected])
    match = any(e in detected for e in expected_set)

    return {
        "expected": expected,
        "triggered": expr,
        "detected": detected,
        "match": match,
        "natural": result.get("natural", False),
        "issues": result.get("issues", ""),
        "raw": result.get("raw", ""),
    }


# ═══════════════════════════════════════════════════════
# CLI commands
# ═══════════════════════════════════════════════════════

async def cmd_snapshot(character: str = "mutsumi"):
    """Take one screenshot and analyze."""
    print("📸 Capturing Live2D screenshot...")
    screenshot = await capture_screenshot()
    if not screenshot:
        print("❌ Screenshot failed - is Live2D running on :19919?")
        return
    print("🤖 Sending to MiMo v2.5 for analysis...")
    result = await analyze_screenshot(screenshot, character)
    print(json.dumps(result, ensure_ascii=False, indent=2))


async def cmd_watch(n: int = 5, interval: float = 3.0, character: str = "mutsumi"):
    """Watch mode: take screenshots every N seconds."""
    print(f"👁 Watch mode: {n} iterations, {interval}s interval")
    for i in range(n):
        print(f"\n── Iteration {i+1}/{n} ──")
        screenshot = await capture_screenshot()
        if not screenshot:
            print("  ❌ Screenshot failed")
            continue
        result = await analyze_screenshot(screenshot, character)
        expr = result.get("expression", "?")
        natural = "✅" if result.get("natural") else "⚠️"
        issues = result.get("issues", "")
        print(f"  Expression: {expr} {natural}")
        if issues:
            print(f"  Issues: {issues}")
        if i < n - 1:
            await asyncio.sleep(interval)


async def cmd_test_emotions(character: str = "mutsumi"):
    """Test all emotions: for each, trigger expression and verify."""
    emotions = list(EMOTION_EXPRESSIONS.keys())
    results = []

    print(f"🧪 Testing {len(emotions)} emotions for character: {character}\n")

    for emotion in emotions:
        print(f"\n🧪 Testing: {emotion}")
        report = await verify_emotion(emotion, character)
        match = "✅" if report["match"] else "❌"
        natural = "✅" if report.get("natural") else "⚠️"
        print(f"  Expected: {emotion} | Triggered: {report.get('triggered', '?')} | Detected: {report['detected']} {match} {natural}")
        if report.get("issues"):
            print(f"  Issues: {report['issues']}")
        results.append(report)

    # Summary
    matched = sum(1 for r in results if r["match"])
    natural_count = sum(1 for r in results if r.get("natural"))
    print(f"\n{'='*50}")
    print(f"📊 Summary: {matched}/{len(results)} expressions matched, {natural_count}/{len(results)} natural")
    print(f"{'='*50}")

    # Save report
    report_path = SCREENSHOT_DIR / f"test_report_{time.strftime('%Y%m%d_%H%M%S')}.json"
    report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"📄 Report saved: {report_path}")


async def cmd_test_all_expressions(character: str = "mutsumi"):
    """Test every single expression available in the model."""
    all_exprs = []
    for exprs in EMOTION_EXPRESSIONS.values():
        all_exprs.extend(exprs)
    all_exprs = sorted(set(all_exprs))

    print(f"🔬 Testing {len(all_exprs)} individual expressions for: {character}\n")

    results = []
    for expr in all_exprs:
        print(f"  📤 {expr}")
        await trigger_expression(expr)
        screenshot = await capture_screenshot()
        if not screenshot:
            print(f"     ❌ Screenshot failed")
            results.append({"expression": expr, "detected": "error", "match": False})
            continue

        result = await analyze_screenshot(screenshot, character)
        detected = result.get("expression", "unknown")
        issues = result.get("issues", "")
        print(f"     Detected: {detected} | Issues: {issues}")
        results.append({
            "expression": expr,
            "detected": detected,
            "issues": issues,
            "natural": result.get("natural", False),
        })

    # Summary
    natural_count = sum(1 for r in results if r.get("natural"))
    print(f"\n📊 {natural_count}/{len(results)} expressions look natural")

    report_path = SCREENSHOT_DIR / f"expr_report_{character}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    report_path.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"📄 Report: {report_path}")


async def cmd_switch(character: str, costume: str):
    """Switch model."""
    await switch_model(character, costume)
    # Take a screenshot to verify
    screenshot = await capture_screenshot()
    if screenshot:
        print("📸 Screenshot taken to verify switch")
    else:
        print("❌ Screenshot failed")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m gateway.live2d_debug [snapshot|watch|test|test-expr|switch] [args]")
        print("  snapshot          - Take one screenshot + analyze")
        print("  watch -n 5        - Watch mode, N iterations")
        print("  test [char]       - Test all emotions (default: mutsumi)")
        print("  test-expr [char]  - Test every individual expression")
        print("  switch <char> <costume> - Switch model")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "snapshot":
        char = sys.argv[2] if len(sys.argv) > 2 else "mutsumi"
        asyncio.run(cmd_snapshot(char))
    elif cmd == "watch":
        n = int(sys.argv[sys.argv.index("-n") + 1]) if "-n" in sys.argv else 5
        char = sys.argv[sys.argv.index("-c") + 1] if "-c" in sys.argv else "mutsumi"
        asyncio.run(cmd_watch(n=n, character=char))
    elif cmd == "test":
        char = sys.argv[2] if len(sys.argv) > 2 else "mutsumi"
        asyncio.run(cmd_test_emotions(char))
    elif cmd == "test-expr":
        char = sys.argv[2] if len(sys.argv) > 2 else "mutsumi"
        asyncio.run(cmd_test_all_expressions(char))
    elif cmd == "switch":
        if len(sys.argv) < 4:
            print("Usage: switch <character> <costume>")
            sys.exit(1)
        asyncio.run(cmd_switch(sys.argv[2], sys.argv[3]))
    else:
        print(f"Unknown command: {cmd}")