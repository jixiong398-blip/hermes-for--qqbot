import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_JUDGE_CONCURRENCY = 100
_judge_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    global _judge_semaphore
    if _judge_semaphore is None:
        _judge_semaphore = asyncio.Semaphore(_JUDGE_CONCURRENCY)
    return _judge_semaphore


def _get_api_key() -> str:
    return os.getenv("DEEPSEEK_API_KEY", "")


def _get_api_base() -> str:
    return os.getenv("DEEPSEEK_BASE_URL", "")


def _get_api_model() -> str:
    return os.getenv("DEEPSEEK_MODEL", "")


JUDGE_SYSTEM_PROMPT = """你是 Soyo 的对话状态判定器。Soyo 是一个 QQ 群聊里的 AI 参与者。
你需要判断 Soyo 在当前这个时刻该不该回复这条消息，以及话题是否应该结束。

## 核心原则

Soyo 默认保持沉默。只有在被明确叫到、或对话直接涉及 Soyo 时才回复。
宁可少说，不要多说。群聊里大部分消息都和 Soyo 无关，不需要插嘴。

但注意：群聊是多人的，群友之间讨论同一个话题很正常。一个话题不会因为"群友之间在聊"就闭合——只要话题还在讨论同一件事，就还在活跃。

## 判定维度

### should_reply — Soyo 该不该说话

该回复的情况（必须明确指向 Soyo）：
- 有人直接 @Soyo 问问题或说话
- 有人在消息里明确叫了 Soyo 的名字（"素世""soyo"）并在对 Soyo 说话
- 有人用 QQ 回复功能回复了 Soyo 的消息
- Soyo 刚说完话，对方直接在回应 Soyo 说的话（即使没有@，只要明显是在对 Soyo 说话就算）
- 之前的对话里 Soyo 正在被追问，即使换了一条消息但明显是同一个人在继续问

不该回复的情况：
- 纯闲聊，和 Soyo 无关（即使话题 Soyo 了解也不主动插嘴）
- 有人提到 Soyo 的名字但是在讨论名字本身，不是在叫 Soyo
- 有人在说"正在同步""别着急""你安静吧"之类的自言自语或对别人说的话
- 恶意调戏或刷屏测试
- 纯图片/卡片/链接分享且无文字引导 Soyo 参与的
- 检测到 bot 之间在循环对话
- 检测到对话内容在空转趋同

### should_end — 话题该不该结束

只有以下情况判 true，否则一律 false：
- 对方明确要求结束对话（"你安静吧""行了别说了""够了"）
- 两个 bot 陷入循环对话（互相说几乎一样的话）
- 对话内容明显空转趋同（来回说废话没新信息）

注意：群友之间混着聊天不算话题结束。即使话题被短暂岔开，
只要没明确表示结束，就判 should_end=false。
不要因为"群友之间在聊"就判话题结束。

不该结束的情况（绝大多数情况都是 false）：
- 对方在追问或补充信息
- 群友在讨论同一话题但没在问 Soyo
- 有人说"好的""嗯"但语气在继续而不是结束
- 有人 @Soyo 开启新话题（这是新话题，不是旧话题结束）

### topic_active — 话题还在不在

true: 话题正在讨论中，没闭合
false: 话题已闭合或被转移

### is_loop — 是否检测到循环/空转

true: 两个 bot 在互相说类似的话，或对话内容在空转趋同
false: 正常对话

## 输出格式

只输出 JSON，不要多余文字：
{"should_reply": true/false, "should_end": true/false, "topic_active": true/false, "is_loop": true/false, "reason": "一句话说明"}"""


def _build_judge_prompt(
    group_name: str,
    attentive_state: str,
    last_reply: str,
    mins_since_reply: float,
    episode_duration: float,
    reply_count: int,
    recent_messages: List[Dict[str, Any]],
    current_msg: Dict[str, Any],
) -> str:
    parts = []
    parts.append(f"群名：{group_name or '未知'}")
    parts.append(f"Soyo 当前状态：{attentive_state}")
    if last_reply:
        parts.append(f"Soyo 上次回复：'{last_reply[:100]}'（{mins_since_reply:.0f}分钟前）")
    else:
        parts.append("Soyo 上次回复：（无，首次被叫或新话题）")
    parts.append(f"本轮话题已持续：{episode_duration:.0f}分钟")
    parts.append(f"本轮 Soyo 已回复：{reply_count}次")
    parts.append("")
    parts.append("## 最近消息（时间正序，最新在下）")
    for m in recent_messages:
        ts = m.get("ts_str", "")
        name = m.get("name", "")
        text = m.get("text", "")[:200]
        is_bot = m.get("is_bot", False)
        tag = " [bot]" if is_bot else ""
        parts.append(f"[{ts}] {name}{tag}: {text}")
    parts.append("")
    parts.append("## 当前消息")
    ts = current_msg.get("ts_str", "")
    name = current_msg.get("name", "")
    text = current_msg.get("text", "")[:300]
    msg_type = current_msg.get("msg_type", "text")
    is_at = current_msg.get("is_at", False)
    at_tag = " 是" if is_at else " 否"
    parts.append(f"[{ts}] {name}: {text}")
    parts.append(f"消息类型：{msg_type}")
    parts.append(f"是否@Soyo：{at_tag}")
    parts.append("")
    parts.append("请判定：")
    return "\n".join(parts)


async def semantic_judge(
    recent_messages: List[Dict[str, Any]],
    current_msg: Dict[str, Any],
    group_name: str = "",
    attentive_state: str = "潜水",
    last_reply: str = "",
    mins_since_reply: float = 0.0,
    episode_duration: float = 0.0,
    reply_count: int = 0,
    timeout: float = 30.0,
) -> Dict[str, Any]:
    async with _get_semaphore():
        api_key = _get_api_key()
        if not api_key or not _get_api_base() or not _get_api_model():
            logger.warning("[SemanticJudge] No API key, defaulting to reply")
            return {"should_reply": True, "should_end": False, "topic_active": True, "is_loop": False, "reason": "no api key fallback"}

        prompt = _build_judge_prompt(
            group_name, attentive_state, last_reply, mins_since_reply,
            episode_duration, reply_count, recent_messages, current_msg,
        )

        try:
            import requests as _r
            _base = _get_api_base()
            _model = _get_api_model()
            resp = await asyncio.to_thread(
                _r.post,
                f"{_base}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": _model,
                    "messages": [
                        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                },
                timeout=timeout,
            )
            data = resp.json()
            msg = data["choices"][0]["message"]
            content = (msg.get("content") or "").strip()
            if not content:
                reasoning = msg.get("reasoning_content") or ""
                import re as _re
                json_match = _re.search(r'\{[^{}]*\}', reasoning)
                if json_match:
                    content = json_match.group()
            if not content:
                raise ValueError("Empty content from LLM (reasoning ate all tokens?)")
            result = json.loads(content)
            result.setdefault("should_reply", True)
            result.setdefault("should_end", False)
            result.setdefault("topic_active", True)
            result.setdefault("is_loop", False)
            result.setdefault("reason", "")
            logger.info(
                "[SemanticJudge] reply=%s end=%s loop=%s reason=%s",
                result["should_reply"], result["should_end"],
                result["is_loop"], result["reason"][:60],
            )
            return result

        except asyncio.TimeoutError:
            logger.warning("[SemanticJudge] Timeout, defaulting to reply for @")
            fallback_r