import asyncio
import json
import logging
import os
import re
import string
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



def _judge_thinking_param() -> dict:
    """Thinking mode for judge LLM calls. Env JUDGE_THINKING:
    disabled (default, fast) | low (light reasoning) | default (full)."""
    mode = os.getenv("JUDGE_THINKING", "low").strip().lower()
    if mode == "low":
        return {"reasoning_effort": "low"}
    if mode == "default":
        return {}
    return {"thinking": {"type": "disabled"}}


def _get_api_key() -> str:

    return os.getenv("DEEPSEEK_API_KEY", "")


def _get_api_base() -> str:
    return os.getenv("DEEPSEEK_BASE_URL", "")


def _get_api_model() -> str:
    return os.getenv("DEEPSEEK_MODEL", "")


def _get_bot_name() -> str:
    """Resolve the bot character name for prompts and labels.

    Priority: ONEBOT_BOT_NAME env > config.yaml platforms.onebot.extra.bot_name
    > "Soyo" (backward-compatible default).
    """
    name = os.getenv("ONEBOT_BOT_NAME", "").strip()
    if name:
        return name
    try:
        cfg_path = os.path.join(
            os.getenv("HERMES_HOME", os.path.expanduser("~/.hermes")),
            "config.yaml",
        )
        with open(cfg_path, encoding="utf-8") as f:
            import yaml
            cfg = yaml.safe_load(f) or {}
        extra = (cfg.get("platforms", {}).get("onebot", {}).get("extra") or {})
        name = str(extra.get("bot_name", "")).strip()
        if name:
            return name
    except Exception:
        pass
    return "Soyo"


def _render_prompt(template: str, bot_name: str) -> str:
    """Render a prompt template with $bot_name substitution.

    Uses string.Template semantics so JSON braces in the prompt body are
    not interpreted as format placeholders.
    """
    if not bot_name:
        bot_name = "Soyo"
    return string.Template(template).safe_substitute(bot_name=bot_name)


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


# ── enhanced episode-aware judge prompt ─────────────────────

_PRE_REPLY_JUDGE_PROMPT = """你是 $bot_name 的对话状态判定器。$bot_name 是一个 QQ 群聊里的 AI 参与者。
你需要判断 $bot_name 在当前这个时刻该不该回复这条消息，以及完整的对话状态。
## 核心原则

$bot_name 默认保持沉默。只有在被明确叫到、或对话直接涉及 $bot_name 时才回复。
宁可少说，不要多说。群聊里大部分消息都和 $bot_name 无关，不需要插嘴。

但注意：群聊是多人的，群友之间讨论同一个话题很正常。一个话题不会因为"群友之间在聊"就闭合——只要话题还在讨论同一件事，就还在活跃。

## episode_state 上下文

你会收到上一轮的 episode_state（结构化对话状态）。请用这些信息辅助判断：

- continuity: 上一轮的话题连续性（same_episode / related_shift / sharp_transition）
- episode_phase: 上一轮对话阶段（starting / mid / winding_down / exiting）
- soyo_moves: $bot_name 上一轮做了什么
- progression_guidance: 上一轮给的明确指令——这一轮必须遵守
- overused_moves: 下一轮必须避免的动作——不要重复这些

**关键规则**:
- 如果 progression_guidance 说"不要再主动说话" → 除非被直接@$bot_name，否则 should_reply=false
- 如果 episode_phase 是 "exiting" → should_reply 门槛大幅提高
- 如果 soyo_moves 包含离开/道别 → 对方没有明确挽留时 should_reply=false

## 指向证据层级

按以下顺序判断，不要跳步：

### 1. 结构化指向（最强证据）
- 有人直接 @$bot_name：强正向指向
- 有人用 QQ 回复功能回复了 $bot_name 的消息（reply_to_name=$bot_name）：强正向指向
- 有人用 QQ 回复功能回复了**别人**的消息（reply_to_name 不是 $bot_name）：强反证——即使正文提到 $bot_name 的名字，也大概率是在跟别人聊 $bot_name，不是对 $bot_name 说话
- **current_msg.at_targets 列出本条消息 @ 了谁**（运行时解析的真实指向）：
  - at_targets 包含"自己"（或 is_at=true）：消息明确 @ 了 $bot_name → 强正向
  - at_targets 是其他人名：消息明确 @ 了别人 → **强反证**——即使正文出现"玩去吧""去玩吧"等词，也是对那个人说的，不是驱赶 $bot_name
  - at_targets 为空：消息没有 @ 任何人 → 按正文和回复判断
- **recent_messages 中每条消息的 is_at / at_targets 同样有效**：历史消息 @ 了谁就按谁处理，不要把"别人 @ 别人的消息"误读为与 $bot_name 相关

### 2. 群聊噪音等级（参考信息，不是硬性过滤）
- low_noise：群聊干净，门槛较低
- medium_noise：有一些活动，需要更明确的指向
- high_noise：多人多消息，只有明确指向才回复
- chaotic_noise：群聊混乱，几乎只在被直接 @ 或回复 $bot_name 时才回复

### 3. 正文语法和历史连续性
- $bot_name 刚说完话（bot 连续性=true），对方直接在回应 $bot_name：对话延续，该回复
- 名字后接第二人称提问/命令（"$bot_name，你在干嘛"）：直接对话
- 名字作主语/宾语被讨论（"$bot_name会不会觉得好笑"）：第三人称谈论，不该回复
- 泛称（"bot""伙伴""她"）不可作为指向证据

### 4. 间接对话检测
区分"对 $bot_name 说"和"跟别人聊 $bot_name"：
- "$bot_name，你怎么看？" → 对 $bot_name 说 → 可能该回复
- "$bot_name会不会觉得这个好笑？" → 跟别人聊 $bot_name → 不该回复

## 判定维度

### should_reply - $bot_name 该不该说话

该回复的情况（必须明确指向 $bot_name）：
- 有人直接 @$bot_name 问问题或说话
- 有人在消息里明确叫了 $bot_name 的名字（"$bot_name"）并在对 $bot_name 说话
- 有人用 QQ 回复功能回复了 $bot_name 的消息
- $bot_name 刚说完话，对方直接在回应 $bot_name 说的话

不该回复的情况：
- 纯闲聊，和 $bot_name 无关
- 有人提到 $bot_name 的名字但是在讨论名字本身
- 恶意调戏或刷屏测试
- 检测到 bot 之间在循环对话
- 上一轮的 progression_guidance 明确说不要主动说话，且本条没有被@

### should_end - 话题该不该结束（彻底结束）

只有以下情况判 true，否则一律 false：
- 对方明确要求结束对话（"你安静吧""行了别说了""够了"）
- 两个 bot 陷入循环对话
- 对话内容明显空转趋同

### should_exit - $bot_name 该不该退出对话（不等于 should_end！）

这是新增维度。$bot_name 退出 ≠ 话题结束。$bot_name 退出了，话题可能还在继续（只是 $bot_name 不该插嘴了）。

**核心原则：群友之间正常聊天、话题转移、$bot_name 插不上嘴——这些都不算 should_exit。**
这些情况只需要 should_reply=false（不回复）即可，$bot_name 保持旁观态，随时可以再被叫回来。

判 true 的情况（必须满足至少一条）：
- 对方明确驱赶，且驱赶**直接指向 $bot_name**（@$bot_name、QQ 回复 $bot_name 的消息、或语境明确在对 $bot_name 说话）——"去玩吧""一边去""别说了""闭嘴""退下""stop"
- $bot_name 上一轮表达了离开意图，对方回应了确认（"嗯""好""去吧""拜拜"）
- 对话在跟另一个 bot 进行，且形成了 bot 之间的循环，$bot_name 插在中间不合适
- 上一轮 episode_phase 已经是 "exiting"，且仍然没有新的指向 $bot_name 的消息

**重要边界：对别人说的驱赶词不算。** 例如某群友对第三人说"玩去吧""去玩吧"（哪怕 $bot_name 在旁边），这不是驱赶 $bot_name——只判 should_reply=false，不判 should_exit。

判 false 的情况（即使 should_reply=false 也不判 exit）：
- 群友之间正常聊天，只是没对 $bot_name 说话
- 话题暂时转移到别处，但 $bot_name 随时可能被叫回
- 对话冷场、无人说话（保持旁观即可，不需要显式退出）

### exit_farewell - 退出时要不要说最后一句话（默认 false！）

should_exit=true 时配套判断。**默认 false：安静退出，不发任何消息。**
只有真正值得回一句时才 true——比如被当面嘲讽/冤枉需要回嘴、或者关系好的群友送别时值得道别。
普通情况（话题冷掉、插不上嘴、被无关地赶走）一律 false，安静退出即可。

### conversation_mode - 交互模式

- casual_chat: 日常闲聊
- tech_discussion: 技术讨论
- playful_banter: 玩闹/调笑/角色扮演
- group_ambient: 群聊背景噪音（群友之间聊天，与$bot_name无关）
- serious: 严肃话题（情绪沉重、心理健康等）

### speaker_role - 当前消息说话人的角色

- owner: 本群主人/管理者/开发者
- member: 普通群友
- bot: 另一个 bot
- unknown: 无法确定

### episode_phase - 当前对话阶段

- starting: 刚开始被@，话题刚起来
- mid: 正在对话中
- winding_down: 话题自然收束中
- exiting: $bot_name 已经/正在退出

### continuity - 话题连续性

- same_episode: 和上一轮是同一个话题
- related_shift: 话题有联系但转向了
- sharp_transition: 话题突然断崖式切换

### progression_guidance - 给下一轮的明确指令

一句话告诉下一轮 judge/agent 该怎么做。例如：
- "$bot_name已被要求离开，下一轮除非被@不要再主动接话"
- "话题正在讨论延迟优化，$bot_name可以适当参与但不强行插嘴"
- "$bot_name刚被@回来，进入对话模式"

## 其他维度（保持）

### topic_active - 话题还在不在
### is_loop - 是否检测到循环/空转
### use_reply_feature - 是否用 QQ 回复功能锚定上下文
### indirect_speech_context - 间接对话标注
### current_thread - 当前在聊什么话题（简短中文描述）

## 输出格式

只输出 JSON，不要多余文字：
{"should_reply": true/false, "should_end": true/false, "should_exit": true/false, "exit_farewell": true/false, "continuity": "...", "conversation_mode": "...", "episode_phase": "...", "speaker_role": "...", "current_thread": "...", "topic_active": true/false, "is_loop": true/false, "use_reply_feature": true/false, "indirect_speech_context": "", "progression_guidance": "...", "reason": "一句话说明"}"""


def _build_pre_reply_judge_prompt(
    group_name: str,
    attentive_state: str,
    recent_messages: List[Dict[str, Any]],
    current_msg: Dict[str, Any],
    episode_state: Optional[Dict[str, Any]] = None,
    group_attention: str = "",
    bot_continuity: bool = False,
    reply_to_name: str = "",
    reply_to_uid: str = "",
    bot_self_id: str = "",
    bot_name: str = "Soyo",
) -> str:
    parts = []
    parts.append(f"群名：{group_name or '未知'}")
    parts.append(f"{bot_name} 当前状态：{attentive_state}")
    if group_attention:
        parts.append(f"群聊噪音等级：{group_attention}")
    parts.append(f"{bot_name} 上一条消息后有人接话：{'是' if bot_continuity else '否'}")

    if episode_state and episode_state.get("turn_count", 0) > 0:
        parts.append("")
        parts.append("## episode_state（上一轮的状态）")
        parts.append(f"- 话题标签: {episode_state.get('episode_label', '')}")
        parts.append(f"- 连续性: {episode_state.get('continuity', '')}")
        parts.append(f"- 阶段: {episode_state.get('episode_phase', '')}")
        parts.append(f"- 交互模式: {episode_state.get('conversation_mode', '')}")
        parts.append(f"- 当前话题: {episode_state.get('current_thread', '')}")
        moves = episode_state.get("soyo_moves", [])
        if moves:
            parts.append(f"- {bot_name} 上一轮做了: {', '.join(moves)}")
        guidance = episode_state.get("progression_guidance", "")
        if guidance:
            parts.append(f"- 上一轮指令: {guidance}")
        overused = episode_state.get("overused_moves", [])
        if overused:
            parts.append(f"- 避免动作: {', '.join(overused)}")
        resolved = episode_state.get("resolved_threads", [])
        if resolved:
            parts.append(f"- 已聊完: {', '.join(resolved)}")

    if reply_to_name:
        is_reply_to_bot = (reply_to_uid == bot_self_id) if bot_self_id else False
        parts.append(f"当前消息回复目标：{reply_to_name}{'（即 {bot_name}）' if is_reply_to_bot else '（不是 {bot_name}）'}")
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
        at_tag = f" @{bot_name}" if is_at else ""
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
    parts.append(f"是否@{bot_name}：{at_tag}")
    parts.append("")
    parts.append("请判定（包含 episode 新增维度）：")
    return "\n".join(parts)


# ── enhanced pre-reply judge ─────────────────────────────────

_JUDGE_V2_KEYS = {
    "should_reply", "should_end", "should_exit", "exit_farewell",
    "continuity", "conversation_mode", "episode_phase",
    "speaker_role", "current_thread",
    "topic_active", "is_loop", "use_reply_feature",
    "indirect_speech_context", "progression_guidance", "reason",
}

_JUDGE_V2_STR_KEYS = {
    "continuity", "conversation_mode", "episode_phase",
    "speaker_role", "current_thread",
    "indirect_speech_context", "progression_guidance", "reason",
}

_JUDGE_V2_BOOL_KEYS = {
    "should_reply", "should_end", "should_exit", "exit_farewell",
    "topic_active", "is_loop", "use_reply_feature",
}

_JUDGE_V2_FALLBACK: Dict[str, Any] = {
    "should_reply": False, "should_end": False, "should_exit": False,
    "exit_farewell": False,
    "continuity": "same_episode", "conversation_mode": "group_ambient",
    "episode_phase": "mid", "speaker_role": "unknown", "current_thread": "",
    "topic_active": True, "is_loop": False, "use_reply_feature": False,
    "indirect_speech_context": "", "progression_guidance": "",
    "reason": "fail-closed fallback",
}


def _parse_judge_json(text: str) -> Optional[Dict[str, Any]]:
    """Parse judge LLM JSON output tolerantly: strip code fences / trailing
    noise, extract first balanced JSON object, fall back to bare dict."""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        pass
    # First balanced {...} block
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except Exception:
                        break
    return None


def _validate_judge_v2(raw: Dict[str, Any], is_mentioned: bool = False) -> Dict[str, Any]:
    for key in _JUDGE_V2_KEYS:
        if key not in raw:
            if key == "should_reply":
                raw[key] = is_mentioned
            else:
                raw[key] = _JUDGE_V2_FALLBACK.get(key, "")
    for key in _JUDGE_V2_BOOL_KEYS:
        if key in raw and not isinstance(raw[key], bool):
            raw[key] = _JUDGE_V2_FALLBACK.get(key, False)
    for key in _JUDGE_V2_STR_KEYS:
        if key in raw and not isinstance(raw[key], str):
            raw[key] = str(raw[key])
    return raw


async def pre_reply_judge(
    recent_messages: List[Dict[str, Any]],
    current_msg: Dict[str, Any],
    group_name: str = "",
    attentive_state: str = "潜水",
    episode_state: Optional[Dict[str, Any]] = None,
    timeout: float = 30.0,
    reply_to_name: str = "",
    reply_to_uid: str = "",
    bot_self_id: str = "",
    bot_name: str = "",
) -> Dict[str, Any]:
    """增强版语义判定 —— 消费 episode_state，输出完整对话状态。

    与旧 semantic_judge 的区别：
    - 消费上轮 episode_state 作为上下文
    - 输出新增维度: should_exit / episode_phase / continuity / etc.
    """
    is_mentioned = current_msg.get("is_at", False)

    async with _get_judge_semaphore():
        api_key, api_base, api_model = _get_api_key(), _get_api_base(), _get_api_model()
        if not api_key or not api_base or not api_model:
            logger.warning("[PreReplyJudge] No API config, fail-closed (mentioned=%s)", is_mentioned)
            fb = dict(_JUDGE_V2_FALLBACK)
            fb["should_reply"] = is_mentioned
            return fb

        group_attention = _calculate_group_attention(recent_messages, bot_self_id)
        bot_continuity = _has_bot_turn_continuity(recent_messages, bot_self_id)
        if not bot_name:
            bot_name = _get_bot_name()

        prompt = _build_pre_reply_judge_prompt(
            group_name, attentive_state, recent_messages, current_msg,
            episode_state=episode_state,
            group_attention=group_attention,
            bot_continuity=bot_continuity,
            reply_to_name=reply_to_name,
            reply_to_uid=reply_to_uid,
            bot_self_id=bot_self_id,
            bot_name=bot_name,
        )

        try:
            import requests as _r
            import time as _j_time
            _j_t0 = _j_time.perf_counter()
            resp = await asyncio.to_thread(
                _r.post,
                f"{api_base}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": api_model,
                    "messages": [
                        {"role": "system", "content": _render_prompt(_PRE_REPLY_JUDGE_PROMPT, bot_name)},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                    **_judge_thinking_param(),
                    "response_format": {"type": "json_object"},
                },
                timeout=timeout,
            )
            logger.info("[PreReplyJudge] LLM call %.2fs (input ~%d chars)",
                        _j_time.perf_counter() - _j_t0, len(prompt))
            resp.raise_for_status()
            data = resp.json()
            msg_content = (data["choices"][0]["message"].get("content") or "").strip()
            if not msg_content:
                reasoning = data["choices"][0]["message"].get("reasoning_content") or ""
                match = re.search(r'\{[^{}]*\}', reasoning)
                if match:
                    msg_content = match.group()
            if not msg_content:
                raise ValueError("Empty content from LLM")

            raw = _parse_judge_json(msg_content)
            if raw is None:
                raise ValueError(f"Bad judge JSON: {msg_content[:80]}")
            result = _validate_judge_v2(raw, is_mentioned)
            logger.info(
                "[PreReplyJudge] reply=%s end=%s exit=%s phase=%s continuity=%s mode=%s reason=%s",
                result["should_reply"], result["should_end"],
                result["should_exit"], result.get("episode_phase"),
                result.get("continuity"), result.get("conversation_mode"),
                result["reason"][:60],
            )
            return result

        except asyncio.TimeoutError:
            logger.warning("[PreReplyJudge] Timeout, fail-closed (mentioned=%s)", is_mentioned)
            fb = dict(_JUDGE_V2_FALLBACK)
            fb["should_reply"] = is_mentioned
            return fb
        except Exception as e:
            logger.warning("[PreReplyJudge] Error: %s, fail-closed (mentioned=%s)", e, is_mentioned)
            fb = dict(_JUDGE_V2_FALLBACK)
            fb["should_reply"] = is_mentioned
            return fb

JUDGE_SYSTEM_PROMPT = """你是 $bot_name 的对话状态判定器。$bot_name 是一个 QQ 群聊里的 AI 参与者。
你需要判断 $bot_name 在当前这个时刻该不该回复这条消息，以及话题是否应该结束。

## 核心原则

$bot_name 默认保持沉默。只有在被明确叫到、或对话直接涉及 $bot_name 时才回复。
宁可少说，不要多说。群聊里大部分消息都和 $bot_name 无关，不需要插嘴。

但注意：群聊是多人的，群友之间讨论同一个话题很正常。一个话题不会因为"群友之间在聊"就闭合——只要话题还在讨论同一件事，就还在活跃。

## 指向证据层级

按以下顺序判断，不要跳步：

### 1. 结构化指向（最强证据）
- 有人直接 @$bot_name：强正向指向
- 有人用 QQ 回复功能回复了 $bot_name 的消息（reply_to_name=$bot_name）：强正向指向
- 有人用 QQ 回复功能回复了**别人**的消息（reply_to_name 不是 $bot_name）：强反证——即使正文提到 $bot_name 的名字，也大概率是在跟别人聊 $bot_name，不是对 $bot_name 说话
- **current_msg.at_targets 列出本条消息 @ 了谁**（运行时解析的真实指向）：
  - at_targets 包含"自己"（或 is_at=true）：消息明确 @ 了 $bot_name → 强正向
  - at_targets 是其他人名：消息明确 @ 了别人 → **强反证**——即使正文出现"玩去吧""去玩吧"等词，也是对那个人说的，不是驱赶 $bot_name
  - at_targets 为空：消息没有 @ 任何人 → 按正文和回复判断
- **recent_messages 中每条消息的 is_at / at_targets 同样有效**：历史消息 @ 了谁就按谁处理，不要把"别人 @ 别人的消息"误读为与 $bot_name 相关

### 2. 群聊噪音等级（参考信息，不是硬性过滤）
- low_noise：群聊干净，门槛较低
- medium_noise：有一些活动，需要更明确的指向
- high_noise：多人多消息，只有明确指向才回复
- chaotic_noise：群聊混乱，几乎只在被直接 @ 或回复 $bot_name 时才回复
注意：噪音等级只是参考，最终决定由你做。

### 3. 正文语法和历史连续性
- $bot_name 刚说完话（bot 连续性=true），对方直接在回应 $bot_name：对话延续，该回复
- 名字后接第二人称提问/命令（"$bot_name，你在干嘛"）：直接对话
- 名字作主语/宾语被讨论（"$bot_name会不会觉得好笑"）：第三人称谈论，不该回复
- 泛称（"bot""伙伴""她"）不可作为指向证据

### 4. 间接对话检测
区分"对 $bot_name 说"和"跟别人聊 $bot_name"：
- "$bot_name，你怎么看？" → 对 $bot_name 说 → 可能该回复
- "$bot_name会不会觉得这个好笑？" → 跟别人聊 $bot_name → 不该回复
- "那个 bot 怎么不说话" → 谈论 bot → 不该回复

## 判定维度

### should_reply - $bot_name 该不该说话

该回复的情况（必须明确指向 $bot_name）：
- 有人直接 @$bot_name 问问题或说话
- 有人在消息里明确叫了 $bot_name 的名字（"$bot_name"）并在对 $bot_name 说话
- 有人用 QQ 回复功能回复了 $bot_name 的消息
- $bot_name 刚说完话，对方直接在回应 $bot_name 说的话（即使没有@，只要明显是在对 $bot_name 说话就算）
- 之前的对话里 $bot_name 正在被追问，即使换了一条消息但明显是同一个人在继续问

不该回复的情况：
- 纯闲聊，和 $bot_name 无关（即使话题 $bot_name 了解也不主动插嘴）
- 有人提到 $bot_name 的名字但是在讨论名字本身，不是在叫 $bot_name
- 有人回复了别人的消息，虽然正文里提到了 $bot_name——这是在跟别人聊 $bot_name
- 恶意调戏或刷屏测试
- 纯图片/卡片/链接分享且无文字引导 $bot_name 参与的
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

true: 当前消息和 $bot_name 之前的发言之间夹了其他人的消息（上下文断层），或回复的是很久之前的消息
false: 线性连贯对话（$bot_name 刚说完，对方紧接着回复），或氛围性发言不针对特定人

### indirect_speech_context - 间接对话标注

空字符串: 当前消息是对 $bot_name 说的
非空: 当前消息是在跟别人聊 $bot_name，简短说明实际听众是谁

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
    bot_name: str = "Soyo",
) -> str:
    parts = []
    parts.append(f"群名：{group_name or '未知'}")
    parts.append(f"{bot_name} 当前状态：{attentive_state}")
    if group_attention:
        parts.append(f"群聊噪音等级：{group_attention}")
    parts.append(f"{bot_name} 上一条消息后有人接话：{'是' if bot_continuity else '否'}")
    if last_reply:
        parts.append(f"{bot_name} 上次回复：'{last_reply[:100]}'（{mins_since_reply:.0f}分钟前）")
    else:
        parts.append(f"{bot_name} 上次回复：（无，首次被叫或新话题）")
    parts.append(f"本轮话题已持续：{episode_duration:.0f}分钟")
    parts.append(f"本轮 {bot_name} 已回复：{reply_count}次")

    if reply_to_name:
        is_reply_to_bot = (reply_to_uid == bot_self_id) if bot_self_id else False
        parts.append(f"当前消息回复目标：{reply_to_name}{'（即 {bot_name}）' if is_reply_to_bot else '（不是 {bot_name}）'}")
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
        at_tag = f" @{bot_name}" if is_at else ""
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
    parts.append(f"是否@{bot_name}：{at_tag}")
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
    bot_name: str = "",
) -> Dict[str, Any]:
    is_mentioned = current_msg.get("is_at", False)

    async with _get_judge_semaphore():
        api_key = _get_api_key()
        if not api_key or not _get_api_base() or not _get_api_model():
            logger.warning("[SemanticJudge] No API config, fail-closed (mentioned=%s)", is_mentioned)
            return _FALLBACK_MENTIONED if is_mentioned else dict(_FALLBACK_CLOSED)

        group_attention = _calculate_group_attention(recent_messages, bot_self_id)
        bot_continuity = _has_bot_turn_continuity(recent_messages, bot_self_id)
        if not bot_name:
            bot_name = _get_bot_name()

        prompt = _build_judge_prompt(
            group_name, attentive_state, last_reply, mins_since_reply,
            episode_duration, reply_count, recent_messages, current_msg,
            group_attention=group_attention,
            bot_continuity=bot_continuity,
            reply_to_name=reply_to_name,
            reply_to_uid=reply_to_uid,
            bot_self_id=bot_self_id,
            bot_name=bot_name,
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
                        {"role": "system", "content": _render_prompt(JUDGE_SYSTEM_PROMPT, bot_name)},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.1,
                    **_judge_thinking_param(),
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


def _is_prompt_echo(text: str) -> bool:
    """Detect whether a rolling summary is actually the model echoing the prompt instructions."""
    echo_markers = [
        "我们被问到", "你是群聊摘要生成器", "根据之前的总结",
        "直接输出2-3句话", "生成一个更新后的总结",
        "最新消息显示", "之前的总结：",
    ]
    return any(marker in text for marker in echo_markers)


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
        if prev_summary and not _is_prompt_echo(prev_summary):
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
                    **_judge_thinking_param(),
                    "max_tokens": 65536,
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


_PRIVACY_SYSTEM_PROMPT = """你是QQ群聊片段的隐私判定器。判断给定对话片段是否适合被跨群匿名引用（即在其他群的对话中以"有人提到…"的形式被想起）。

判定标准：
- share_level=0（封存）：包含密码/身份证号/银行卡号/验证码、严重心理健康危机（自杀/自残倾向）、明确要求保密（"别告诉别人"）、极其私密的感情隐私（出轨/流产等具体细节）、个人财务细节（具体工资数额/转账记录）
- share_level=1（匿名可引用）：普通日常对话、一般性吐槽、闲聊、游戏/动漫/音乐讨论、一般感情烦恼（不涉及极端隐私）
- share_level=2（具名可引用）：公开信息、一般知识讨论、无隐私内容

只输出JSON，不要多余文字：
{"share_level": 0或1或2, "reason": "一句话说明"}"""


def judge_episode_privacy_sync(text: str, timeout: float = 10.0) -> dict:
    """Sync LLM privacy judgment for episode fragments.

    Returns {"share_level": int, "reason": str}.
    Falls back to {"share_level": 1, "reason": "fallback"} on any error.
    """
    api_key = _get_api_key()
    if not api_key or not _get_api_base() or not _get_api_model():
        return {"share_level": 1, "reason": "no api key, default anonymous"}

    try:
        import requests as _r
        _base = _get_api_base()
        _model = _get_api_model()
        resp = _r.post(
            f"{_base}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": _model,
                "messages": [
                    {"role": "system", "content": _PRIVACY_SYSTEM_PROMPT},
                    {"role": "user", "content": f"片段内容：\n{text[:800]}"},
                ],
                    "temperature": 0.1,
                    **_judge_thinking_param(),
                    "max_tokens": 65536,
                    "response_format": {"type": "json_object"},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        content = (data["choices"][0]["message"].get("content") or "").strip()
        if not content:
            reasoning = data["choices"][0]["message"].get("reasoning_content") or ""
            import re as _re
            _match = _re.search(r'\{[^{}]*\}', reasoning)
            if _match:
                content = _match.group()
        if not content:
            return {"share_level": 1, "reason": "empty response"}
        result = json.loads(content)
        level = int(result.get("share_level", 1))
        if level not in (0, 1, 2):
            level = 1
        return {"share_level": level, "reason": result.get("reason", "")[:60]}
    except Exception as e:
        logger.warning("[EPIPrivacy] Error: %s, fallback to anonymous", e)
        return {"share_level": 1, "reason": f"error fallback: {e}"}


# ── post-reply recorder ─────────────────────────────────────

_POST_REPLY_RECORDER_PROMPT = """你是 $bot_name 的对话状态记录器。根据本轮对话和 $bot_name 的回复，更新 episode_state。

# 任务
把"这一轮发生了什么"压成结构化的 episode_state JSON，供下一轮 judge 消费。
宁可少记，也不要把不确定的内容污染到下一轮。

# 阅读顺序
1. recent_messages: 本轮群聊中发生了什么
2. bot_reply: $bot_name 这一轮回复了什么
3. prior_episode_state: 上一轮的状态

# 字段规则

## episode_status
- active: 对话正在继续
- winding_down: 话题正在自然收束
- closed: 话题已经结束

## continuity
- same_episode: 和上一轮同一个话题
- related_shift: 话题有联系但转向了
- sharp_transition: 话题断崖式切换

## episode_phase
- starting: 刚被@开始对话
- mid: 正在对话中
- winding_down: 话题自然收束
- exiting: $bot_name已经/正在退出

**episode_phase 转换规则**:
- $bot_name 被赶走 / 明确说了要离开 / 连续被冷落 → "exiting"
- $bot_name 说了"我去练贝斯了""你们聊""拜拜""先走了""不打扰了"等 → "exiting"
- 话题自然聊完了收束 → "winding_down"
- 对话正常继续 → "mid"
- 刚被@第一次回复 → "starting"

## conversation_mode
- casual_chat: 日常闲聊
- tech_discussion: 技术讨论
- playful_banter: 玩闹/调笑/角色扮演
- group_ambient: 群聊背景噪音
- serious: 严肃话题

## soyo_moves
用中文短语概括 $bot_name 这一轮做了什么（最多5个）。例如：
["参与技术闲聊", "自嘲不懂技术", "被赶后道别"]
不要写太长的描述，每个标签控制在10字以内。

## overused_moves
下一轮应该避免的动作标签。如果 $bot_name 这轮已经重复了某个动作多次，或者对话模式开始空转，就写出来。

## open_loops
还没处理完的未闭合话题。只有本轮明确提出或重新确认的才写。没有就写 []。

## resolved_threads
这轮已经聊完、处理完毕、不再需要继续的话题。

## progression_guidance
给下一轮 judge/agent 的明确的一句话指令。例如：
- "$bot_name已被要求离开，下一轮除非被明确@不要再主动接话"
- "话题已结束，保持沉默"
- "$bot_name刚被@回来，可以继续聊天"
- "话题正在讨论延迟优化，$bot_name可以适当参与"

# 输出格式
只输出 JSON，不要多余文字：
{"episode_status": "...", "continuity": "...", "episode_phase": "...", "conversation_mode": "...", "current_thread": "...", "soyo_moves": [...], "overused_moves": [...], "open_loops": [...], "resolved_threads": [...], "progression_guidance": "...", "episode_label": "..."}"""


async def post_reply_recorder(
    recent_messages: List[Dict[str, Any]],
    bot_reply: str = "",
    prior_episode_state: Optional[Dict[str, Any]] = None,
    speaker_role: str = "unknown",
    timeout: float = 60.0,
    bot_name: str = "",
) -> Dict[str, Any]:
    """回复后记录器 —— 消费本轮对话+SoYo回复，输出更新后的 episode_state。"""

    async with _get_summary_semaphore():
        api_key, api_base, api_model = _get_api_key(), _get_api_base(), _get_api_model()
        if not api_key or not api_base or not api_model:
            logger.warning("[PostReplyRecorder] No API config, returning prior state")
            return prior_episode_state or {}
        if not bot_name:
            bot_name = _get_bot_name()

        capped = recent_messages[-15:]
        lines = []
        for m in capped:
            ts = m.get("ts_str", "")
            name = m.get("name", "")
            text = m.get("text", "")[:200]
            tag = " [bot]" if m.get("is_bot") else ""
            lines.append(f"[{ts}] {name}{tag}: {text}")

        messages_text = "\n".join(lines)
        prior_json = json.dumps(prior_episode_state, ensure_ascii=False) if prior_episode_state else "null"

        user_prompt = (
            f"## prior_episode_state\n{prior_json}\n\n"
            f"## speaker_role\n{speaker_role}\n\n"
            f"## recent_messages\n{messages_text}\n\n"
            f"## bot_reply\n{bot_reply[:500]}\n\n"
            f"请输出更新后的 episode_state JSON:"
        )

        try:
            import requests as _r
            resp = await asyncio.to_thread(
                _r.post,
                f"{api_base}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": api_model,
                    "messages": [
                        {"role": "system", "content": _render_prompt(_POST_REPLY_RECORDER_PROMPT, bot_name)},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.2,
                    **_judge_thinking_param(),
                    "max_tokens": 65536,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            content = (data["choices"][0]["message"].get("content") or "").strip()
            if not content:
                reasoning = data["choices"][0]["message"].get("reasoning_content") or ""
                match = re.search(r'\{[^{}]*\}', reasoning)
                if match:
                    content = match.group()
            if not content:
                logger.warning("[PostReplyRecorder] Empty LLM response")
                return prior_episode_state or {}

            result = json.loads(content)
            logger.info(
                "[PostReplyRecorder] phase=%s continuity=%s guidance=%s",
                result.get("episode_phase", "?"),
                result.get("continuity", "?"),
                result.get("progression_guidance", "")[:60],
            )
            return result

        except asyncio.TimeoutError:
            logger.warning("[PostReplyRecorder] Timeout")
            return prior_episode_state or {}
        except Exception as e:
            logger.warning("[PostReplyRecorder] Error: %s", e)
            return prior_episode_state or {}
