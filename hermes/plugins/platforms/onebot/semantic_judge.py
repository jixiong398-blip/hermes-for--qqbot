import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_JUDGE_CONCURRENCY = 16
_SUMMARY_CONCURRENCY = 8
_judge_semaphore: Optional[asyncio.Semaphore] = None
_summary_semaphore: Optional[asyncio.Semaphore] = None


def _get_judge_semaphore() -> asyncio.Semaphore:
    global _judge_semaphore
    if _judge_semaphore is None:
        _judge_semaphore = asyncio.Semaphore(_JUDGE_CONCURRENCY)
    return _judge_semaphore


def _get_summary_semaphore() -> asyncio.Semaphore:
    global _summary_semaphore
    if _summary_semaphore is None:
        _summary_semaphore = asyncio.Semaphore(_SUMMARY_CONCURRENCY)
    return _summary_semaphore


def _get_api_key() -> str:
    return os.getenv("DEEPSEEK_API_KEY", "")


def _get_api_base() -> str:
    return os.getenv("DEEPSEEK_BASE_URL", "")


def _get_api_model() -> str:
    return os.getenv("DEEPSEEK_MODEL", "")


# ── expected JSON schema ────────────────────────────────────

_EXPECTED_KEYS = {"should_reply", "should_end", "topic_active", "is_loop", "use_reply_feature", "indirect_speech_context", "reason"}

_FALLBACK_CLOSED: Dict[str, Any] = {
    "should_reply": False,
    "should_end": False,
    "topic_active": True,
    "is_loop": False,
    "use_reply_feature": False,
    "indirect_speech_context": "",
    "reason": "fail-closed fallback",
}

_FALLBACK_MENTIONED: Dict[str, Any] = {
    "should_reply": True,
    "should_end": False,
    "topic_active": True,
    "is_loop": False,
    "use_reply_feature": False,
    "indirect_speech_context": "",
    "reason": "fail-closed (mentioned)",
}


def _validate_judge_result(raw: Dict[str, Any], is_mentioned: bool = False) -> Dict[str, Any]:
    for key in _EXPECTED_KEYS:
        if key not in raw:
            if key == "should_reply":
                raw[key] = is_mentioned
            else:
                raw[key] = _FALLBACK_CLOSED[key]
    for k, v in raw.items():
        if k in ("should_reply", "should_end", "topic_active", "is_loop", "use_reply_feature"):
            if not isinstance(v, bool):
                raw[k] = _FALLBACK_CLOSED[k]
    return raw


# ── noise level ─────────────────────────────────────────────

_ACTIVE_WINDOW_SECONDS = 180
_ACTIVE_WINDOW_MAX_MESSAGES = 10


def _calculate_group_attention(
    recent_messages: List[Dict[str, Any]],
    bot_self_id: str = "",
) -> str:
    capped = recent_messages[-_ACTIVE_WINDOW_MAX_MESSAGES:]
    if not capped:
        return "low_noise"
    non_bot = [m for m in capped if not m.get("is_bot", False)]
    if not non_bot:
        return "low_noise"
    has_at_bot = any(m.get("is_at", False) for m in capped)
    if has_at_bot:
        return "low_noise"
    distinct_speakers = {m.get("name", "") for m in non_bot if m.get("name")}
    if len(distinct_speakers) >= 3 and len(non_bot) >= 4:
        return "chaotic_noise"
    if len(distinct_speakers) >= 2 and len(non_bot) >= 2:
        return "high_noise"
    if len(non_bot) >= 4:
        return "high_noise"
    if non_bot:
        return "medium_noise"
    return "low_noise"


def _has_bot_turn_continuity(
    recent_messages: List[Dict[str, Any]],
    bot_self_id: str = "",
) -> bool:
    """Check if the message immediately before current was from the bot.
    Caller MUST pass recent_messages WITHOUT the current message as last entry.
    """
    if not recent_messages:
        return False
    latest = recent_messages[-1]
    return latest.get("is_bot", False)


# ── judge prompt ────────────────────────────────────────────

JUDGE_SYSTEM_PROMPT = """你是 Soyo 的对话状态判定器。Soyo 是一个 QQ 群聊里的 AI 参与者。
你需要判断 Soyo 在当前这个时刻该不该回复这条消息，以及话题是否应该结束。

## 核心原则

Soyo 默认保持沉默。只有在被明确叫到、或对话直接涉及 Soyo 时才回复。
宁可少说，不要多说。群聊里大部分消息都和 Soyo 无关，不需要插嘴。

但注意：群聊是多人的，群友之间讨论同一个话题很正常。一个话题不会因为"群友之间在聊"就闭合——只要话题还在讨论同一件事，就还在活跃。

## 指向证据层级

按以下顺序判断，不要跳步：

### 1. 结构化指向（最强证据）
- 有人直接 @Soyo：强正向指向
- 有人用 QQ 回复功能回复了 Soyo 的消息（reply_to_name=Soyo）：强正向指向
- 有人用 QQ 回复功能回复了**别人**的消息（reply_to_name 不是 Soyo）：强反证——即使正文提到 Soyo 的名字，也大概率是在跟别人聊 Soyo，不是对 Soyo 说话

### 2. 群聊噪音等级（参考信息，不是硬性过滤）
- low_noise：群聊干净，门槛较低
- medium_noise：有一些活动，需要更明确的指向
- high_noise：多人多消息，只有明确指向才回复
- chaotic_noise：群聊混乱，几乎只在被直接 @ 或回复 Soyo 时才回复
注意：噪音等级只是参考，最终决定由你做。

### 3. 正文语法和历史连续性
- Soyo 刚说完话（bot 连续性=true），对方直接在回应 Soyo：对话延续，该回复
- 名字后接第二人称提问/命令（"素世，你在干嘛"）：直接对话
- 名字作主语/宾语被讨论（"素世会不会觉得好笑"）：第三人称谈论，不该回复
- 泛称（"bot""伙伴""她"）不可作为指向证据

### 4. 间接对话检测
区分"对 Soyo 说"和"跟别人聊 Soyo"：
- "素世，你怎么看？" → 对 Soyo 说 → 可能该回复
- "素世会不会觉得这个好笑？" → 跟别人聊 Soyo → 不该回复
- "那个 bot 怎么不说话" → 谈论 bot → 不该回复

## 判定维度

### should_reply - Soyo 该不该说话

该回复的情况（必须明确指向 Soyo）：
- 有人直接 @Soyo 问问题或说话
- 有人在消息里明确叫了 Soyo 的名字（"素世""soyo"）并在对 Soyo 说话
- 有人用 QQ 回复功能回复了 Soyo 的消息
- Soyo 刚说完话，对方直接在回应 Soyo 说的话（即使没有@，只要明显是在对 Soyo 说话就算）
- 之前的对话里 Soyo 正在被追问，即使换了一条消息但明显是同一个人在继续问

不该回复的情况：
- 纯闲聊，和 Soyo 无关（即使话题 Soyo 了解也不主动插嘴）
- 有人提到 Soyo 的名字但是在讨论名字本身，不是在叫 Soyo
- 有人回复了别人的消息，虽然正文里提到了 Soyo——这是在跟别人聊 Soyo
- 恶意调戏或刷屏测试
- 纯图片/卡片/链接分享且无文字引导 Soyo 参与的
- 检测到 bot 之间在循环对话
- 检测到对话内容在空转趋同

### should_end - 话题该不该结束

只有以下情况判 true，否则一律 false：
- 对方明确要求结束对话（"你安静吧""行了别说了""够了"）
- 两个 bot 陷入循环对话（互相说几乎一样的话）
- 对话内容明显空转趋同（来回说废话没新信息）

注意：群友之间混着聊天不算话题结束。即使话题被短暂岔开，
只要没明确表示结束，就判 should_end=false。

### topic_active - 话题还在不在

true: 话题正在讨论中，没闭合
false: 话题已闭合或被转移

### is_loop - 是否检测到循环/空转

true: 两个 bot 在互相说类似的话，或对话内容在空转趋同
false: 正常对话

### use_reply_feature - 是否用 QQ 回复功能锚定上下文

true: 当前消息和 Soyo 之前的发言之间夹了其他人的消息（上下文断层），或回复的是很久之前的消息
false: 线性连贯对话（Soyo 刚说完，对方紧接着回复），或氛围性发言不针对特定人

### indirect_speech_context - 间接对话标注

空字符串: 当前消息是对 Soyo 说的
非空: 当前消息是在跟别人聊 Soyo，简短说明实际听众是谁

## 输出格式

只输出 JSON，不要多余文字：
{"should_reply": true/false, "should_end": true/false, "topic_active": true/false, "is_loop": true/false, "use_reply_feature": true/false, "indirect_speech_context": "", "reason": "一句话说明"}"""


def _build_judge_prompt(
    group_name: str,
    attentive_state: str,
    last_reply: str,
    mins_since_reply: float,
    episode_duration: float,
    reply_count: int,
    recent_messages: List[Dict[str, Any]],
    current_msg: Dict[str, Any],
    group_attention: str = "",
    bot_continuity: bool = False,
    reply_to_name: str = "",
    reply_to_uid: str = "",
    bot_self_id: str = "",
) -> str:
    parts = []
    parts.append(f"群名：{group_name or '未知'}")
    parts.append(f"Soyo 当前状态：{attentive_state}")
    if group_attention:
        parts.append(f"群聊噪音等级：{group_attention}")
    parts.append(f"Soyo 上一条消息后有人接话：{'是' if bot_continuity else '否'}")
    if last_reply:
        parts.append(f"Soyo 上次回复：'{last_reply[:100]}'（{mins_since_reply:.0f}分钟前）")
    else:
        parts.append("Soyo 上次回复：（无，首次被叫或新话题）")
    parts.append(f"本轮话题已持续：{episode_duration:.0f}分钟")
    parts.append(f"本轮 Soyo 已回复：{reply_count}次")

    if reply_to_name:
        is_reply_to_bot = (reply_to_uid == bot_self_id) if bot_self_id else False
        parts.append(f"当前消息回复目标：{reply_to_name}{'（即 Soyo）' if is_reply_to_bot else '（不是 Soyo）'}")
    else:
        parts.append("当前消息回复目标：无（非回复消息）")

    parts.append("")
    parts.append("## 最近消息（时间正序，最新在下）")
    for m in recent_messages:
        ts = m.get("ts_str", "")
        name = m.get("name", "")
        text = m.get("text", "")[:200]
        is_bot = m.get("is_bot", False)
        is_at = m.get("is_at", False)
        tag = " [bot]" if is_bot else ""
        at_tag = " @Soyo" if is_at else ""
        parts.append(f"[{ts}] {name}{tag}{at_tag}: {text}")
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


# ── semantic judge ──────────────────────────────────────────

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
    reply_to_name: str = "",
    reply_to_uid: str = "",
    bot_self_id: str = "",
) -> Dict[str, Any]:
    is_mentioned = current_msg.get("is_at", False)

    async with _get_judge_semaphore():
        api_key = _get_api_key()
        if not api_key or not _get_api_base() or not _get_api_model():
            logger.warning("[SemanticJudge] No API config, fail-closed (mentioned=%s)", is_mentioned)
            return _FALLBACK_MENTIONED if is_mentioned else dict(_FALLBACK_CLOSED)

        group_attention = _calculate_group_attention(recent_messages, bot_self_id)
        bot_continuity = _has_bot_turn_continuity(recent_messages, bot_self_id)

        prompt = _build_judge_prompt(
            group_name, attentive_state, last_reply, mins_since_reply,
            episode_duration, reply_count, recent_messages, current_msg,
            group_attention=group_attention,
            bot_continuity=bot_continuity,
            reply_to_name=reply_to_name,
            reply_to_uid=reply_to_uid,
            bot_self_id=bot_self_id,
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
            resp.raise_for_status()
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
                raise ValueError("Empty content from LLM")

            raw = json.loads(content)
            result = _validate_judge_result(raw, is_mentioned)
            logger.info(
                "[SemanticJudge] reply=%s end=%s loop=%s noise=%s continuity=%s reply_to=%s reason=%s",
                result["should_reply"], result["should_end"],
                result["is_loop"], group_attention, bot_continuity,
                reply_to_name or "none", result["reason"][:60],
            )
            return result

        except asyncio.TimeoutError:
            logger.warning("[SemanticJudge] Timeout, fail-closed (mentioned=%s)", is_mentioned)
            return _FALLBACK_MENTIONED if is_mentioned else dict(_FALLBACK_CLOSED)
        except Exception as e:
            logger.warning("[SemanticJudge] Error: %s, fail-closed (mentioned=%s)", e, is_mentioned)
            return _FALLBACK_MENTIONED if is_mentioned else dict(_FALLBACK_CLOSED)


# ── rolling summary ─────────────────────────────────────────

_SUMMARY_SYSTEM_PROMPT = """你是群聊摘要生成器。直接输出2-3句话的中文总结，聚焦：讨论了什么、谁参与、有无结论。
禁止输出"根据之前的总结""最新消息显示"等废话。不要解释你在做什么，直接给出总结本身。"""


async def generate_rolling_summary(
    recent_messages: List[Dict[str, Any]],
    prev_summary: str = "",
    timeout: float = 15.0,
) -> str:
    async with _get_summary_semaphore():
        api_key = _get_api_key()
        if not api_key or not _get_api_base() or not _get_api_model():
            return prev_summary

        capped = recent_messages[-20:]
        lines = []
        for m in capped:
            ts = m.get("ts_str", "")
            name = m.get("name", "")
            text = m.get("text", "")[:150]
            tag = " [bot]" if m.get("is_bot") else ""
            lines.append(f"[{ts}] {name}{tag}: {text}")

        user_prompt = "\n".join(lines)
        if prev_summary:
            user_prompt = f"之前：{prev_summary}\n\n{user_prompt}"

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
                        {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 300,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            content = (data["choices"][0]["message"].get("content") or "").strip()
            if not content:
                reasoning = data["choices"][0]["message"].get("reasoning_content") or ""
                import re as _re
                _match = _re.search(r'[\s\S]{10,}', reasoning)
                if _match:
                    content = _match.group()[:300]
            if not content:
                return prev_summary
            logger.info("[RollingSummary] Updated: %s", content[:80])
            return content
        except Exception as e:
            logger.warning("[RollingSummary] Error: %s, keeping prev", e)
            return prev_summary
