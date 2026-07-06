"""
Live2D Auto Controller — MiMo v2.5-powered emotion dispatch.

Two-tier classification:
  Tier 1: MiMo v2.5 LLM (primary, via opencode.ai) — understands context & tone
  Tier 2: Keyword matching (fallback, ~0ms) — handles obvious patterns

Runs inside the Gateway, hooks into the reply pipeline.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from typing import Optional

logger = logging.getLogger("hermes.gateway.live2d_auto")

# ═══════════════════════════════════════════════════════
# Emotion → Expression / Motion
# ═══════════════════════════════════════════════════════

EXPRESSIONS = {
    "happy":     ["smile01","smile02","smile03","smile04","smile05","smile06"],
    "sad":       ["sad01","sad02","sad03"],
    "cry":       ["cry01","cry02"],
    "angry":     ["angry01","angry02","angry03","angry04"],
    "surprised": ["surprised01"],
    "thinking":  ["thinking01","thinking02"],
    "serious":   ["serious01","serious02","serious03","serious04"],
    "shy":       ["shame01","shame02"],
    "nervous":   ["odoodo01"],
    "relieved":  ["ando01"],
    "wink":      ["wink01"],
    "neutral":   ["default","idle01"],
}

MOTIONS = {
    "happy":     ["smile01","smile02","smile03","smile04","smile05","smile06"],
    "sad":       ["sad01","sad02"],
    "cry":       ["cry01","cry02"],
    "angry":     ["angry02","angry03","angry04"],
    "surprised": ["surprised01"],
    "thinking":  ["thinking01","thinking02_01","thinking02_02"],
    "serious":   ["serious01","serious02","kime01"],
    "shy":       ["shame01","shame02"],
    "nervous":   ["odoodo01"],
    "relieved":  ["ando01"],
    "wink":      ["wink01"],
    "neutral":   ["idle01","nf01","nf02","nf03","nf04","nf05"],
}

# ═══════════════════════════════════════════════════════
# Tier 2: Keyword fallback (used when MiMo unavailable)
# ═══════════════════════════════════════════════════════

KEYWORD_RULES = [
    (re.compile(r"233+|哈哈+|笑死|草$|乐了|好耶|太好|不错|恭喜|nice|棒|可爱|喜欢|开心|高兴|嘿嘿|嘻嘻|诶嘿"), "happy"),
    (re.compile(r"难过|伤心|哭了|泪|呜呜|emo|破防|可惜|遗憾|唉"), "sad"),
    (re.compile(r"生气|气死|火大|过分|烦|讨厌|离谱|无语"), "angry"),
    (re.compile(r"居然|竟|天哪|不是吧|什么[？！!]|不会吧|震惊|卧槽"), "surprised"),
    (re.compile(r"嗯[—~]|唔[—~]|让我想|等等|emmm|hmm"), "thinking"),
    (re.compile(r"害羞|不好意思|别说了|别说|羞耻|脸红"), "shy"),
    (re.compile(r"紧张|害怕|担心|不安|焦虑"), "nervous"),
    (re.compile(r"还好|幸好|松了口气|放心|安心|呼"), "relieved"),
    (re.compile(r"秘密|不告诉|悄悄|你猜|懂的都懂"), "wink"),
]


def keyword_classify(text: str) -> Optional[str]:
    """Fast keyword match. Returns emotion or None."""
    for pattern, emotion in KEYWORD_RULES:
        if pattern.search(text):
            return emotion
    return None


# ═══════════════════════════════════════════════════════
# Tier 1: MiMo v2.5 LLM classifier
# ═══════════════════════════════════════════════════════

MIMO_EMOTION_PROMPT = """你是素世的情绪感知模块。根据素世的回复内容，判断她此刻的情绪。

情绪选项（只输出一个词）:
- happy:    开心、高兴、被逗乐
- sad:      难过、失落、可惜
- angry:    生气、不满、无语
- surprised: 惊讶、意外
- thinking: 思考中、犹豫
- shy:      害羞、不好意思
- nervous:  紧张、担心
- relieved: 松了一口气、安心
- wink:     俏皮、开玩笑、使眼色
- neutral:  平静、无所谓、默认状态

素世的回复:
{text}

情绪（只输出一个词）:"""


async def mimo_classify(text: str, config: dict) -> Optional[str]:
    """Use MiMo v2.5 via environment-configured endpoint to classify emotion from text."""
    import httpx
    import os

    api_key = os.environ.get("MIMO_API_KEY") or os.environ.get("XIAOMI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    base_url = os.environ.get("MIMO_BASE_URL") or "https://opencode.ai/zen/go/v1"
    model = os.environ.get("MIMO_MODEL") or "mimo-v2.5"

    if not api_key:
        logger.debug("MiMo: no API key in env, skipping LLM classify")
        return None

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "user", "content": MIMO_EMOTION_PROMPT.format(text=text[:300])}
                    ],
                    "max_tokens": 10,
                    "temperature": 0.3,
                },
            )
            if resp.status_code != 200:
                logger.debug(f"MiMo HTTP {resp.status_code}")
                return None
            data = resp.json()
            emotion = data["choices"][0]["message"]["content"].strip().lower()
            valid = {"happy","sad","angry","surprised","thinking","shy","nervous","relieved","wink","neutral"}
            if emotion in valid:
                return emotion
            logger.debug(f"MiMo returned unknown emotion: {emotion}")
            return None
    except Exception as e:
        logger.debug(f"MiMo classify failed: {e}")
        return None


# ═══════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════

async def classify_emotion(text: str, config: dict) -> str:
    """
    Two-tier emotion classification.
    Returns: emotion name (happy/sad/angry/.../neutral)
    """
    # Tier 2: keyword first (fast, reliable for obvious patterns)
    kw = keyword_classify(text)
    if kw:
        return kw

    # Tier 1: MiMo v2.5 for nuanced classification
    mimo = await mimo_classify(text, config)
    if mimo:
        return mimo

    return "neutral"


def pick_expression(emotion: str) -> tuple[str, Optional[str]]:
    """Pick expression + motion for an emotion. Returns (expr, motion_or_none)."""
    exps = EXPRESSIONS.get(emotion, EXPRESSIONS["neutral"])
    mots = MOTIONS.get(emotion, MOTIONS["neutral"])
    expr = random.choice(exps)
    motion = random.choice(mots) if mots else None
    return expr, motion
