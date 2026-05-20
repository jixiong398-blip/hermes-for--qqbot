"""
Short-Term Memory (STM) — QQ群场景优化版

QQ群真实场景特点:
  - 中文为主, 话题跳跃快, 多人交替发言
  - 潜水策略: 没@就不回, 未参与的对话不该消耗记忆
  - DM 和群聊行为差异大: DM 全保留, 群聊需要更强的噪声过滤

优化:
  - 中文 N-gram 关键词提取 (不依赖 jieba, 零外部依赖)
  - 发言者身份追踪 (群友 vs bot vs @mention 对象)
  - 群聊窗口减半, 潜水消息权重降低
  - 情感色调复用 TTS emotion 关键词体系
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .store import MemoryStore, ShortTermEntry

logger = logging.getLogger(__name__)

# QQ 群聊 vs DM 差异化参数
DM_WINDOW_SIZE = 30
GROUP_WINDOW_SIZE = 15
DM_SUMMARY_TRIGGER = 24
GROUP_SUMMARY_TRIGGER = 10

# 中文情感关键词 (与 ts_adapter.py emotion 体系对齐)
CHINESE_EMOTION_MAP = {
    "happy": ["开心", "高兴", "快乐", "太好了", "哈哈", "笑死", "233", "好耶", "nice", "赞"],
    "sad": ["难过", "悲伤", "哭了", "呜呜", "难受", "心累", "emo", "抑郁"],
    "angry": ["生气", "愤怒", "可恶", "滚", "tm", "傻逼", "无语", "恶心"],
    "surprise": ["震惊", "惊讶", "卧槽", "哇", "天哪", "不是吧", "什么鬼"],
    "question": ["怎么", "为什么", "?", "？", "如何", "能不能", "可以吗", "请问"],
    "apologize": ["对不起", "抱歉", "不好意思", "我的锅", "我错了"],
    "agree": ["对的", "没错", "好的", "确实", "是的", "有道理", "说得好"],
    "flirty": ["么么哒", "抱抱", "撒娇", "~", "贴贴", "亲亲"],
    "neutral": [],
}

# 中文高频话题词典 (常见 QQ 群讨论领域)
CN_TOPIC_DICT = {
    "编程": ["python", "java", "go", "rust", "代码", "bug", "部署", "服务器", "api",
              "github", "docker", "linux", "数据库", "前端", "后端", "框架"],
    "AI": ["ai", "gpt", "llm", "模型", "训练", "推理", "transformer", "agent",
           "prompt", "openai", "claude", "deepseek", "embedding", "向量"],
    "音乐": ["mygo", "bangdream", "贝斯", "吉他", "鼓", "live", "演唱会", "翻唱", "编曲"],
    "二次元": ["动漫", "番剧", "声优", "轻小说", "cos", "漫展", "手办", "同人"],
    "游戏": ["原神", "崩铁", "绝区零", "lol", "瓦", "steam", "switch", "ps5"],
    "生活": ["吃饭", "睡觉", "上班", "上学", "考试", "加班", "放假", "旅游", "猫", "狗"],
}


def extract_chinese_topics(text: str) -> List[str]:
    """中英文混合话题提取, 零外部依赖.
    使用 2-gram 中文词 + 词典匹配 + 英文关键词."""
    topics = set()

    if not text:
        return []

    text_lower = text.lower()

    # 1. 词典匹配 — 检查已知话题领域
    for domain, keywords in CN_TOPIC_DICT.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                topics.add(domain)
                break  # 一个领域只加一次

    # 2. 中文 2-gram 提取 — 连续中文字符对
    chinese_chars = re.findall(r'[\u4e00-\u9fff]+', text)
    for chunk in chinese_chars:
        if len(chunk) >= 2:
            for i in range(len(chunk) - 1):
                bigram = chunk[i:i+2]
                # 过滤无意义的虚词对
                if not _is_stop_bigram(bigram):
                    topics.add(bigram)

    # 3. 英文技术关键词
    eng_keywords = re.findall(r'\b[a-zA-Z]{3,15}\b', text_lower)
    common_stop = {"the", "and", "for", "you", "that", "this", "with", "have", "from",
                   "your", "what", "are", "can", "not", "but", "all", "was", "just",
                   "like", "about", "when", "how", "will", "get", "out", "some", "more"}
    for kw in eng_keywords:
        if kw not in common_stop:
            topics.add(kw)

    # 返回前 5 个最有意义的话题
    return list(topics)[:5]


def _is_stop_bigram(bigram: str) -> bool:
    """过滤中文停用 2-gram (虚词组合)."""
    stop_bigrams = {
        "一个", "这个", "那个", "什么", "怎么", "为什么", "可以",
        "不是", "我们", "他们", "你们", "自己", "没有", "已经",
        "还是", "或者", "但是", "因为", "所以", "如果", "虽然",
        "然后", "就是", "的话", "来说", "时候", "现在", "不过",
        "这样", "那样", "觉得", "知道", "应该", "可能", "一定",
    }
    return bigram in stop_bigrams


def detect_chinese_emotion(text: str) -> str:
    """检测中文消息的情感色调."""
    for emotion, keywords in CHINESE_EMOTION_MAP.items():
        for kw in keywords:
            if kw.lower() in text.lower():
                return emotion
    return "neutral"


def detect_chinese_intent(text: str) -> str:
    """检测中文消息的意图类型."""
    text_l = text.lower()

    # 提问
    if re.search(r'[?？]|怎么|如何|为什么|能不能|可以吗|请问|有没有|是什么', text_l):
        return "question"
    # 请求/命令
    if re.search(r'^(帮|帮我|帮忙|能不能帮|请|麻烦|求)', text_l):
        return "request"
    # 分享/陈述
    if re.search(r'^(分享|推荐|安利|记录|今天|昨天|最近)', text_l):
        return "share"
    # 吐槽
    if re.search(r'(无语|离谱|笑死|蚌埠|绷不住|难绷|逆天|6)', text_l):
        return "comment"
    # @提及
    if re.search(r'@\w+', text_l):
        return "mention"

    return "chat"


class ShortTermMemory:
    """会话级短期记忆, QQ 群场景优化."""

    def __init__(self, store: MemoryStore, window_size: int = DM_WINDOW_SIZE,
                 summary_trigger: int = DM_SUMMARY_TRIGGER):
        self._store = store
        self.dm_window = DM_WINDOW_SIZE
        self.group_window = GROUP_WINDOW_SIZE
        self.dm_summary_trigger = DM_SUMMARY_TRIGGER
        self.group_summary_trigger = GROUP_SUMMARY_TRIGGER

    def add_turn(self, session_id: str, turn_index: int, role: str,
                 content: str, speaker_name: str = "",
                 chat_type: str = "dm", bot_replied: bool = True,
                 topics: Optional[List[str]] = None,
                 intent: str = "", emotional_tone: str = "") -> int:
        """记录一轮对话.

        Args:
            session_id: 会话ID (DM=用户ID, 群聊=group:群号)
            role: user/assistant/other_user
            speaker_name: QQ 发言者昵称 (群聊时必须)
            chat_type: dm 或 group
            bot_replied: 机器人是否回复了这条消息 (潜水时=False)
        """
        if not topics and content:
            topics = extract_chinese_topics(content)
        if not emotional_tone and content:
            emotional_tone = detect_chinese_emotion(content)
        if not intent and content and role in ("user", "other_user"):
            intent = detect_chinese_intent(content)

        entry = ShortTermEntry(
            session_id=session_id,
            turn_index=turn_index,
            role=role,
            speaker_name=speaker_name,
            chat_type=chat_type,
            content=content[:3000],
            topics=topics or [],
            intent=intent,
            emotional_tone=emotional_tone,
            bot_replied=bot_replied,
            created_at=datetime.now(timezone.utc).timestamp(),
        )
        return self._store.add_short_term(entry)

    def get_recent(self, session_id: str, n: int = 10,
                   chat_type: str = "dm",
                   bot_replied_only: bool = False) -> List[ShortTermEntry]:
        """获取最近 N 条未摘要的对话.

        Args:
            bot_replied_only: 群聊场景下, True=只看bot参与过的对话, False=全部
        """
        entries = self._store.get_session_entries(session_id, last_n=n * 2)
        if chat_type == "group" and bot_replied_only:
            entries = [e for e in entries if e.bot_replied]
        return entries[-n:]

    def get_recent_as_messages(self, session_id: str, n: int = 10) -> List[Dict[str, str]]:
        entries = self.get_recent(session_id, n)
        return [{"role": e.role, "content": e.content} for e in entries]

    def needs_summarization(self, session_id: str, chat_type: str = "dm") -> bool:
        trigger = self.group_summary_trigger if chat_type == "group" else self.dm_summary_trigger
        entries = self._store.get_session_entries(session_id, last_n=trigger + 1)
        return len(entries) > trigger

    def get_session_summary_context(self, session_id: str,
                                     chat_type: str = "dm") -> str:
        """构建群聊上下文, 按发言者分组."""
        window = self.group_window if chat_type == "group" else self.dm_window
        entries = self.get_recent(session_id, n=window, chat_type=chat_type,
                                  bot_replied_only=(chat_type == "group"))
        if not entries:
            return ""

        if chat_type == "group":
            lines = ["## 群聊最近对话 (仅保留机器人参与的部分)\n"]
            for e in entries:
                speaker = e.speaker_name or ("机器人" if e.role == "assistant" else "群友")
                topic_str = f" [{', '.join(e.topics)}]" if e.topics else ""
                line = f"**{speaker}**{topic_str}: {e.content[:300]}"
                lines.append(line)
        else:
            lines = ["## 最近对话\n"]
            for e in entries:
                prefix = "用户" if e.role == "user" else "机器人"
                topic_str = f" [{', '.join(e.topics)}]" if e.topics else ""
                lines.append(f"**{prefix}**{topic_str}: {e.content[:500]}")

        return "\n".join(lines)

    def get_topics(self, session_id: str) -> List[str]:
        return self._store.get_unsummarized_topics(session_id)

    def get_session_stats(self, session_id: str) -> Dict[str, Any]:
        entries = self.get_recent(session_id, n=100)
        topics = {}
        tones = {}
        speakers = {}
        for e in entries:
            for t in e.topics:
                topics[t] = topics.get(t, 0) + 1
            if e.emotional_tone:
                tones[e.emotional_tone] = tones.get(e.emotional_tone, 0) + 1
            if e.speaker_name:
                speakers[e.speaker_name] = speakers.get(e.speaker_name, 0) + 1

        return {
            "total_turns": len(entries),
            "topic_frequency": sorted(topics.items(), key=lambda x: -x[1])[:10],
            "emotional_distribution": tones,
            "dominant_topic": max(topics, key=topics.get) if topics else None,
            "active_speakers": sorted(speakers.items(), key=lambda x: -x[1])[:5],
        }

    def extract_topics_simple(self, message: str) -> List[str]:
        """委托给中文提取器."""
        return extract_chinese_topics(message)

    def clear_session(self, session_id: str):
        self._store.mark_summarized(session_id, 999999)
