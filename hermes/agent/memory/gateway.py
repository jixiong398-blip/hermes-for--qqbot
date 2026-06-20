"""
Unified Memory Gateway — single entry point for all memory operations.

Coordinates all memory subsystems:
  - ShortTermMemory: conversation tracking per session
  - LongTermMemory: persistent facts/knowledge
  - WorkflowMemory: procedural patterns with decay
  - WikiKnowledgeBase: external knowledge integration
  - MemoryConsolidator: STM→LTM→WFM promotion
  - MemoryRetriever: multi-source recall with scoring

Usage:
  gateway = UnifiedMemoryGateway()
  
  # Per-turn processing
  gateway.process_turn(session_id, turn_index, "user", "How do I deploy?")
  
  # Recall at start of agent turn
  context = gateway.get_context_for_agent("How do I deploy?", session_id)
  
  # End-of-session consolidation (runs on session close/reset)
  gateway.consolidate(session_id)
  
  # Apply forgetting decay (call periodically)
  gateway.maintenance_cycle()
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .store import MemoryStore, _memory_db_path
from .short_term import ShortTermMemory
from .long_term import LongTermMemory
from .workflow import WorkflowMemory, DECAY_MIN_WEIGHT
from .wiki import WikiKnowledgeBase, KARPATHY_WIKI_REPO
from .retrieval import MemoryRetriever
from .consolidation import MemoryConsolidator

logger = logging.getLogger(__name__)


class UnifiedMemoryGateway:
    """Single entry point for the unified memory system.

    Provides:
      - Per-turn message processing with topic extraction
      - Multi-source recall for agent context enrichment
      - Automatic STM→LTM→WFM consolidation
      - Usage-based workflow decay (forgetting)
      - Wiki knowledge base auto-integration
      - System prompt block generation
      - Memory maintenance scheduling
    """

    _instances: Dict[str, "UnifiedMemoryGateway"] = {}
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls, name: str = "default") -> "UnifiedMemoryGateway":
        """Get or create a singleton instance (per name)."""
        with cls._lock:
            if name not in cls._instances:
                cls._instances[name] = cls(name=name)
            return cls._instances[name]

    def __init__(self, name: str = "default",
                 db_path: Optional[Path] = None,
                 wiki_dirs: Optional[List[Path]] = None,
                 enable_wiki: bool = True,
                 enable_workflow_decay: bool = True,
                 consolidation_min_turns: int = 6):
        self.name = name
        self._store = MemoryStore(db_path=db_path or _memory_db_path())
        self._stm = ShortTermMemory(self._store)
        self._ltm = LongTermMemory(self._store)
        self._wfm = WorkflowMemory(self._store)
        self._wiki = WikiKnowledgeBase(
            self._store,
            wiki_dirs=wiki_dirs or [],
            github_repos=[KARPATHY_WIKI_REPO] if enable_wiki else [],
        )
        self._retriever = MemoryRetriever(
            self._store, self._stm, self._ltm, self._wfm, self._wiki,
        )
        self._consolidator = MemoryConsolidator(
            self._store, self._stm, self._ltm, self._wfm,
        )
        self._consolidation_min_turns = consolidation_min_turns
        self._enable_workflow_decay = enable_workflow_decay
        self._enable_wiki = enable_wiki
        self._turn_counters: Dict[str, int] = {}
        self._last_maintenance: float = 0.0
        self._wiki_synced: bool = False
        self._obsidian_vault: Any = None

    # ── Per-Turn Processing ───────────────────────────────────

    def process_turn(self, session_id: str, role: str, content: str,
                     speaker_name: str = "",
                     chat_type: str = "dm",
                     bot_replied: bool = True,
                     topics: Optional[List[str]] = None,
                     intent: str = "",
                     emotional_tone: str = "") -> int:
        """Record a conversation turn in STM.

        Args:
            session_id: 会话ID
            role: user / assistant / other_user
            speaker_name: 发言者昵称 (群聊时必须)
            chat_type: dm 或 group
            bot_replied: 机器人是否回复了 (潜水=False)
        """
        turn_index = self._turn_counters.get(session_id, 0) + 1
        self._turn_counters[session_id] = turn_index

        if not topics and content:
            topics = self._stm.extract_topics_simple(content)

        entry_id = self._stm.add_turn(
            session_id=session_id,
            turn_index=turn_index,
            role=role,
            content=content,
            speaker_name=speaker_name,
            chat_type=chat_type,
            bot_replied=bot_replied,
            topics=topics,
            intent=intent,
            emotional_tone=emotional_tone,
        )
        return entry_id

    # ── Recall / Context Retrieval ────────────────────────────

    def recall(self, query: str, session_id: Optional[str] = None,
               max_chars: int = 4000) -> str:
        """Unified recall — returns formatted context for the LLM.

        This is the main method to call before an agent turn to
        inject relevant memories into the context.
        """
        # Auto-inject wiki context
        wiki_context = ""
        if self._enable_wiki:
            wiki_context = self._wiki.auto_context_injection(query)

        # Multi-source recall
        recall_prompt = self._retriever.build_recall_prompt(
            query, session_id, max_chars=max_chars,
        )

        # Match and inject relevant workflows
        wf_context = ""
        matched_wfs = self._wfm.match_trigger(query)
        if matched_wfs:
            wf_lines = ["## Matched Workflows\n"]
            for wf in matched_wfs[:3]:
                steps = " -> ".join(wf.steps[:5]) if wf.steps else "no steps defined"
                wf_lines.append(
                    f"- **{wf.name}** ({wf.current_weight:.2f}): {wf.description[:100]}\n"
                    f"  Steps: {steps}"
                )
            wf_context = "\n".join(wf_lines)

        # Obsidian vault search (auto-injected like wiki)
        obsidian_context = ""
        try:
            obsidian_context = self.get_obsidian_context(query, max_chars=1500)
        except Exception:
            pass

        parts = [p for p in [recall_prompt, wf_context, wiki_context, obsidian_context] if p]
        return "\n\n".join(parts)

    def get_context_for_agent(self, user_message: str,
                               session_id: Optional[str] = None,
                               chat_type: str = "dm") -> str:
        """Get the complete memory context to inject into the agent's prompt."""
        context = self.recall(user_message, session_id)
        # Prepend STM context with chat_type awareness
        if session_id:
            stm_context = self._stm.get_session_summary_context(session_id, chat_type)
            if stm_context:
                context = stm_context + "\n\n" + context
        return context

    def get_stm_context(self, session_id: str, chat_type: str = "dm") -> str:
        """Get short-term conversation context."""
        return self._stm.get_session_summary_context(session_id, chat_type)

    # ── Prompt Block Generation ───────────────────────────────

    def build_memory_prompt_block(self, max_chars: int = 3000) -> str:
        """Build a comprehensive memory block for the system prompt.

        Includes: LTM facts, active workflows. STM is per-turn,
        so it's injected separately via get_context_for_agent().
        """
        parts = []

        # Long-term memory block
        ltm_block = self._ltm.build_prompt_block(max_chars=max_chars // 2)
        if ltm_block:
            parts.append(ltm_block)

        # Active workflows block
        wf_block = self._wfm.build_prompt_block(active_only=True)
        if wf_block:
            parts.append(wf_block)

        return "\n\n".join(parts)

    # ── Consolidation (Session End) ───────────────────────────

    def consolidate(self, session_id: str) -> Dict[str, Any]:
        """Run the full consolidation pipeline at session end.

        This is the "sleep phase" that converts ephemeral session data
        into persistent structured memories.
        """
        stats = self._consolidator.consolidate(session_id)
        return stats

    def consolidate_if_needed(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Run consolidation only if enough turns have accumulated.

        Checks both the in-memory turn counter (for active sessions)
        and the actual STM database entries (for sessions recorded
        by other processes, e.g. the agent subprocess).
        """
        # Check in-memory counter for sessions still live in this process
        turn_count = self._turn_counters.get(session_id, 0)
        if turn_count >= self._consolidation_min_turns:
            return self.consolidate(session_id)

        # Check database entries — covers sessions where process_turn()
        # was called in the agent subprocess rather than the gateway
        try:
            entries = self.stm.get_recent(session_id, n=self._consolidation_min_turns)
            if len(entries) >= self._consolidation_min_turns:
                return self.consolidate(session_id)
        except Exception:
            pass

        return None

    # ── Maintenance & Forgetting ──────────────────────────────

    def maintenance_cycle(self) -> Dict[str, Any]:
        """Run a full maintenance cycle: decay, pruning, cleanup.

        Should be called periodically (e.g., every hour or on startup).
        """
        now = datetime.now(timezone.utc).timestamp()
        self._last_maintenance = now

        stats = {
            "workflow_decay": [],
            "workflows_pruned": [],
            "stm_pruned": 0,
        }

        # Apply workflow decay
        if self._enable_workflow_decay:
            decay_changes = self._wfm.apply_decay_all()
            stats["workflow_decay"] = [
                {"name": name, "old": old, "new": new}
                for name, old, new in decay_changes
            ]

            # Prune forgotten workflows
            pruned = self._wfm.prune_forgotten(dry_run=False)
            stats["workflows_pruned"] = pruned

        # Prune old STM entries: delete older than 1 hour OR keep only 200 newest
        self._store.prune_short_term(max_age_days=0.04)
        # Also trim by count — keep at most 200 most recent
        conn = self._store._get_conn()
        row = conn.execute(
            "SELECT id FROM short_term_entries ORDER BY created_at DESC LIMIT 1 OFFSET 200"
        ).fetchone()
        if row:
            conn.execute("DELETE FROM short_term_entries WHERE id <= ?", (row[0],))
            conn.commit()
            stats["stm_pruned"] = row[0]
        else:
            stats["stm_pruned"] = 0

        # ── L1: Ebbinghaus forgetting (replaces old TTL step decay) ──
        forget_stats = self._ltm.recompute_all_forgetting()
        stats["forgetting"] = forget_stats
        if forget_stats["deleted"] or forget_stats["updated"]:
            logger.info("L1 forgetting: deleted %d, updated %d memories",
                        forget_stats["deleted"], forget_stats["updated"])

        # Lightweight maintenance (FTS5 rebuild, pragma optimize)
        # Full VACUUM with freelist threshold is only run when needed
        self._store.quick_maintenance()

        # ── LLM-based memory distillation from chat history ──
        self._distill_from_chat_buffer()

        # ── Auto-update SOUL.md with recent memories ──
        self._sync_soul_md()

        return stats

    # ── LLM Chat Buffer Distillation ─────────────────────────

    def _distill_from_chat_buffer(self) -> None:
        """Extract facts from recent group chat messages using LLM and store in LTM.
        
        Reads chat_message_buffer from state.db, formats recent messages as
        context, sends to LLM for structured fact extraction, and stores
        results as long_term_entries.
        """
        import json as _json, sqlite3 as _sqlite3, time as _time, os as _os
        from pathlib import Path as _Path
        
        db_path = _Path.home() / ".hermes" / "state.db"
        if not db_path.exists():
            return
        
        try:
            # Read recent messages (last 6 hours, up to 200 messages)
            db = _sqlite3.connect(str(db_path))
            cutoff = _time.time() - 21600  # 6 hours
            rows = db.execute(
                "SELECT chat_id, user_id, sender_name, content FROM chat_message_buffer "
                "WHERE created_at > ? AND is_bot != 1 "
                "AND content NOT LIKE '[图片]%' "
                "ORDER BY id DESC LIMIT 200",
                (cutoff,),
            ).fetchall()
            db.close()
            
            if len(rows) < 10:
                return  # not enough data
            
            # Group by chat_id and format
            groups: dict = {}
            for gid, uid, name, text in rows:
                uid_str = str(uid) if uid and int(uid) > 10000 else "?"
                groups.setdefault(gid, []).append(f"({uid_str}) {name}: {text[:200]}")
            
            # Build prompt for each group (max 2 groups)
            # 限制每周期最多15条，避免洪水
            _max_per_cycle = 15
            new_facts = 0
            for gid, msgs in list(groups.items())[:2]:
                if len(msgs) < 5 or new_facts >= _max_per_cycle:
                    continue
                chat_text = "\n".join(reversed(msgs[-30:]))
                facts = self._call_distill_llm(chat_text)
                if facts:
                    for cat, key, val, conf, *extra in facts:
                        if new_facts >= _max_per_cycle:
                            break
                        tags = extra[0] if extra else []
                        # 置信度门槛：低于0.5不存
                        if conf < 0.5:
                            continue
                        # 冲突检测：同key已存在→更新或跳过
                        try:
                            existing = self._ltm.get_fact(cat, key)
                            if existing:
                                days_old = (_time.time() - existing.created_at) / 86400.0
                                # 更新条件：旧条目>30天 或 新事实置信度更高
                                if days_old > 30 or conf > existing.confidence + 0.1:
                                    self.add_long_term(cat, key, val, tags=tags, confidence=conf)
                                    new_facts += 1
                                continue
                        except Exception:
                            pass
                        # 相似key冲突检测：同QQ号、key后缀相似但值不同→可能是更新
                        qq = key.split("_")[0] if "_" in key and key.split("_")[0].isdigit() else ""
                        key_suffix = key[len(qq)+1:] if qq else ""
                        if qq and len(key_suffix) >= 2:
                            try:
                                conn2 = self._store._get_conn()
                                similar_rows = conn2.execute(
                                    "SELECT id, key, value, confidence FROM long_term_entries "
                                    "WHERE key LIKE ? AND category = ? AND key != ?",
                                    (f"{qq}%", cat, key),
                                ).fetchall()
                                for sid, skey, sval, sconf in similar_rows:
                                    s_suffix = skey[len(qq)+1:] if "_" in skey else ""
                                    suffix_overlap = len(set(key_suffix) & set(s_suffix)) / max(len(set(key_suffix) | set(s_suffix)), 1)
                                    if suffix_overlap > 0.5 and conf > sconf:
                                        self._ltm.update_confidence(sid, -0.2)
                            except Exception:
                                pass
                        try:
                            self.add_long_term(cat, key, val, tags=tags, confidence=conf)
                            new_facts += 1
                        except Exception:
                            pass
            
            if new_facts:
                logger.info("Distillation: %d new facts from %d groups", new_facts, min(2, len(groups)))
                
        except Exception as e:
            logger.warning("Distillation failed: %s", e, exc_info=True)
    
    def _call_distill_llm(self, chat_text: str) -> list:
        """Call LLM to extract structured facts from chat transcript.
        
        Returns list of (category, key, value, confidence) tuples.
        """
        import requests as _r, json as _json, os as _os
        from pathlib import Path as _P
        
        api_key = _os.getenv("HERMES_API_KEY", "")
        base_url = _os.getenv("HERMES_BASE_URL", "https://opencode.ai/zen/go/v1")
        
        # Try to read from config.yaml
        try:
            import yaml as _y
            cfg = _y.safe_load((_P.home() / ".hermes" / "config.yaml").read_text())
            model_cfg = cfg.get("model", {})
            api_key = model_cfg.get("api_key", api_key)
            base_url = model_cfg.get("base_url", base_url)
            _distill_model = model_cfg.get("model", "deepseek-chat")
        except Exception as e:
            logger.warning("Distill: cannot read config: %s", e)
        
        if not api_key:
            logger.warning("Distill: no API key found")
            return []
        
        prompt = (
            "你是一个记忆提取助手。从下面的群聊记录中提取值得长期记忆的事实。\n"
            f"【当前日期】{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
            f"今天是 {datetime.now(timezone.utc).strftime('%Y年%m月%d日')}。所有相对日期（明天/后天/下周/昨天）必须转为绝对日期（YYYY-MM-DD）。\n\n"
            "【核心规则】\n"
            "1. 每条事实必须标注发言人，key 格式：QQ号_话题。QQ号在每条消息开头括号里。\n"
            "2. 只提取真实的、有价值的偏好/习惯/知识。群聊里90%的对话是玩笑和角色扮演，不要当真。\n"
            '3. 如果聊天里有人说"搞错了""不是这样的""早就过了""你记错了"等纠正语——这不是新事实，不要提取。\n'
            "4. 明显的玩笑、抽象话、网络梗、配菜话题一律跳过。\n"
            "5. 同一话题只在7天内记录一次，不要重复。\n"
            "6. 如果没有值得长期记忆的内容，输出空数组 []。\n\n"
            "【置信度标准】\n"
            "0.8-1.0: 用户明确陈述的事实（如【我是做前端开发的】【我在北京】）\n"
            "0.5-0.7: 从对话中合理推断的偏好（如多次提到喜欢某事物）\n"
            "0.3-0.4: 弱信号，宁可不存也不要存错的\n"
            "低于0.5的不要输出。\n\n"
            "【类别】只允许以下5种类别：\n"
            "user_preference: 用户的喜好/厌恶/习惯\n"
            "user_profile: 用户的身份/技能/背景\n"
            "knowledge: 有价值的知识/信息\n"
            "decision: 用户明确表达的决定/计划\n"
            "relationship: 真实的人际关系（不是角色扮演）\n\n"
            "【标签】每条事实标注2-5个关键词标签，帮助后续检索关联：\n"
            "格式: [类别, key, 值, 置信度, [标签列表]]\n\n"
            "示例:\n"
            '[\n  ["user_profile", "2910137276_开发环境", "{{CHANNEL_NAME}}使用Ubuntu+GNOME开发环境", 0.8, ["开发", "Ubuntu", "GNOME"]],\n'
            '  ["user_preference", "2276279679_贝斯偏好", "Tomoris喜欢Fender Precision贝斯", 0.6, ["贝斯", "Fender", "乐器"]]\n'
            ']\n\n'
            f"群聊记录:\n{chat_text}\n\n"
            "输出 JSON 数组，没有值得记的就输出 []。不要其他文字。"
        )
        
        try:
            r = _r.post(
                f"{base_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": _distill_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                },
                timeout=60,
            )
            if r.status_code != 200:
                logger.warning("Distill LLM returned %d: %s", r.status_code, r.text[:200])
                return []
            data = r.json()
            content = data["choices"][0]["message"]["content"].strip()
            
            # Parse JSON
            if not content:
                logger.warning("Distill: empty response from LLM")
                return []
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("\n```", 1)[0]
            facts = _json.loads(content)
            logger.info("Distill: extracted %d facts", len(facts))
            
            # Normalize confidence: map strings to floats
            _conf_map = {"high": 0.8, "medium": 0.5, "low": 0.3, "none": 0.1}
            result = []
            for f in facts:
                if len(f) < 4:
                    continue
                conf = f[3]
                if isinstance(conf, str):
                    conf = _conf_map.get(conf.lower(), 0.4)
                tags = list(f[4]) if len(f) >= 5 and isinstance(f[4], list) else []
                result.append((str(f[0]), str(f[1]), str(f[2]), float(conf), tags))
            return result
        except Exception as e:
            logger.warning("Distill LLM call failed: %s", e)
            return []

    def _sync_soul_md(self) -> None:
        """Append recent high-confidence LTM facts to SOUL.md '我的记忆' section."""
        from pathlib import Path
        soul_path = Path.home() / ".hermes" / "SOUL.md"
        if not soul_path.exists():
            return
        try:
            content = soul_path.read_text(encoding="utf-8")
        except Exception:
            return

        # Read recent high-confidence facts (last 30 days, confidence >= 0.5)
        conn = self._store._get_conn()
        cutoff = self._store._now() - (30 * 86400)
        rows = conn.execute(
            "SELECT category, key, value, confidence FROM long_term_entries "
            "WHERE confidence >= 0.5 AND created_at > ? "
            "AND category NOT IN ('qzone','qzone_log','qzone_posts','qzone_post','cron','general','sticker') "
            "ORDER BY created_at DESC LIMIT 20",
            (cutoff,),
        ).fetchall()

        if not rows:
            return

        # Format memory entries
        memory_lines = []
        seen = set()
        for cat, key, val, conf in rows:
            # Dedup by category+key in this batch
            sig = f"{cat}:{key}"
            if sig in seen:
                continue
            seen.add(sig)
            text = val.strip()[:150]
            if text:
                memory_lines.append(f"- {text}")

        if not memory_lines:
            return

        new_block = "\n## 我的记忆\n\n" + "\n".join(memory_lines) + "\n"

        # Replace existing "我的记忆" section or append
        import re
        marker = "## 我的记忆"
        if marker in content:
            # Find the marker and replace everything after it until next ## or end
            idx = content.index(marker)
            # Find next ## section after the marker
            rest = content[idx + len(marker):]
            next_section = re.search(r'\n## ', rest)
            if next_section:
                content = content[:idx + len(marker)] + "\n\n" + "\n".join(memory_lines) + "\n" + rest[next_section.start():]
            else:
                content = content[:idx + len(marker)] + "\n\n" + "\n".join(memory_lines) + "\n"
        else:
            content = content.rstrip() + "\n\n" + marker + "\n\n" + "\n".join(memory_lines) + "\n"

        try:
            soul_path.write_text(content, encoding="utf-8")
        except Exception:
            pass

    # ── Wiki Sync ─────────────────────────────────────────────

    def sync_wiki(self, force: bool = False) -> Dict[str, int]:
        """Synchronize the wiki knowledge base."""
        if not self._enable_wiki:
            return {"status": "wiki_disabled"}

        stats = self._wiki.sync(force=force)
        self._wiki_synced = True
        return stats

    def ensure_wiki_synced(self):
        """Sync wiki if not yet synced."""
        if self._enable_wiki and not self._wiki_synced:
            try:
                self.sync_wiki()
            except Exception as e:
                logger.warning("Wiki sync failed: %s", e)

    # ── Obsidian Vault ─────────────────────────────────────────

    @property
    def obsidian(self):
        """Lazy-load the Obsidian vault with auto-index on first access."""
        if self._obsidian_vault is None:
            from agent.memory.obsidian import ObsidianVault
            from pathlib import Path

            vault_path = Path(os.environ.get("OBSIDIAN_VAULT_PATH", "E:/ai/knowledge"))
            if not vault_path.exists():
                vault_path = Path.home() / "Documents" / "Obsidian"

            try:
                self._obsidian_vault = ObsidianVault(vault_path)
            except Exception as e:
                import traceback
                logger.error("Obsidian vault creation failed: %s\n%s", e, traceback.format_exc())
                return None
            try:
                self._obsidian_vault.index()
            except Exception as e:
                import traceback
                logger.error("Obsidian vault index failed: %s\n%s", e, traceback.format_exc())
        return self._obsidian_vault

    def index_obsidian(self, force: bool = False) -> Dict[str, int]:
        """Index the Obsidian vault."""
        vault = self.obsidian
        if vault is None:
            return {"added": 0, "updated": 0, "skipped": 0}
        return vault.index(force=force)

    def search_obsidian(self, query: str, top_k: int = 5) -> List[Dict]:
        """Search the Obsidian vault."""
        vault = self.obsidian
        if vault is None:
            return []
        results = vault.search(query, top_k=top_k)
        return [
            {
                "title": note.title,
                "path": note.rel_path,
                "score": round(score, 3),
                "tags": note.tags[:10],
                "is_moc": note.is_moc,
                "snippet": note.snippet(800),
                "linked_notes": note.wikilinks[:5],
            }
            for note, score in results
        ]

    def get_obsidian_context(self, query: str, max_chars: int = 2000) -> str:
        """Get obsidian search results as prompt context."""
        vault = self.obsidian
        if vault is None:
            return ""
        return vault.build_search_context(query, max_chars=max_chars)

    def get_obsidian_stats(self) -> Dict:
        """Get obsidian vault statistics."""
        vault = self.obsidian
        if vault is None:
            return {
                "vault_path": "",
                "total_notes": 0,
                "total_links": 0,
                "total_backlinks": 0,
                "moc_notes": 0,
                "unique_tags": 0,
                "tags": [],
            }
        return vault.stats()

    # ── LTM Manual Operations ─────────────────────────────────

    def add_long_term(self, category: str, key: str, value: str,
                      tags: Optional[List[str]] = None,
                      confidence: float = 0.5) -> int:
        """Manually add a long-term memory fact."""
        return self._ltm.add_fact(category, key, value, tags, confidence)

    def search_long_term(self, query: str, limit: int = 10) -> List[Dict]:
        """Search long-term memory."""
        results = self._ltm.search(query, limit)
        return [
            {"id": r.id, "category": r.category, "key": r.key,
             "value": r.value, "confidence": r.confidence}
            for r in results
        ]

    def delete_long_term(self, entry_id: int):
        """Delete a long-term memory fact."""
        self._ltm.delete_fact(entry_id)

    # ── Workflow Manual Operations ────────────────────────────

    def add_workflow(self, name: str, description: str, steps: List[str],
                     trigger_patterns: Optional[List[str]] = None,
                     base_weight: float = 1.0) -> int:
        """Manually add a workflow."""
        return self._wfm.add_workflow(
            name=name, description=description, steps=steps,
            trigger_patterns=trigger_patterns, base_weight=base_weight,
        )

    def search_workflows(self, query: str) -> List[Dict]:
        """Search workflows."""
        wfs = self._wfm.get_relevant_workflows(query)
        return [
            {"name": w.name, "description": w.description,
             "weight": w.current_weight, "usage": w.usage_count,
             "success": w.success_count}
            for w in wfs
        ]

    def record_workflow_use(self, name: str, success: bool = True):
        """Record a workflow being used (manual trigger)."""
        self._wfm.record_usage(name, success)

    # ── Stats & Diagnostics ───────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive memory system statistics."""
        return {
            "store": self._store.get_store_stats(),
            "wiki": self._wiki.get_stats(),
            "active_sessions": len(self._turn_counters),
            "last_maintenance": self._last_maintenance,
            "workflow_decay_enabled": self._enable_workflow_decay,
            "wiki_enabled": self._enable_wiki,
        }

    def get_workflow_decay_report(self) -> List[Dict]:
        """Get a report of workflow weights for monitoring."""
        wfs = self._wfm._store.get_all_workflows()
        return [
            {
                "name": w.name,
                "current_weight": w.current_weight,
                "usage_count": w.usage_count,
                "success_rate": w.success_count / max(1, w.usage_count),
                "last_used_days_ago": (
                    (datetime.now(timezone.utc).timestamp() - w.last_used) / 86400.0
                    if w.last_used > 0 else float("inf")
                ),
                "status": (
                    "forgotten" if w.current_weight <= DECAY_MIN_WEIGHT
                    else "decaying" if w.current_weight < 0.3
                    else "active"
                ),
            }
            for w in wfs
        ]

    # ── Lifecycle ─────────────────────────────────────────────

    def on_session_start(self, session_id: str):
        """Called when a new session starts."""
        self._turn_counters[session_id] = 0
        self.ensure_wiki_synced()

    def on_session_end(self, session_id: str):
        """Called when a session ends. Triggers consolidation."""
        self.consolidate_if_needed(session_id)
        if session_id in self._turn_counters:
            del self._turn_counters[session_id]

    def correct(self, target_id: int, new_value: str,
                 new_confidence: float = 0.85, reason: str = "",
                 source_user_id: str = "") -> Optional[dict]:
        result = self._ltm.supersede_fact(
            target_id, new_value, new_confidence,
            source_user_id=source_user_id,
        )
        if result:
            self._store.record_ltm_retrieval(result["new_id"])
        return result

    def doubt(self, target_id: int, reason: str = "") -> dict:
        self._ltm.mark_doubt(target_id)
        return {"ok": True, "id": target_id}

    def link(self, src_id: int, dst_id: int, relation: str,
              weight: float = 0.5) -> Optional[int]:
        conn = self._store._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        try:
            row = conn.execute(
                """INSERT INTO memory_edges (src_id, dst_id, relation, weight, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (src_id, dst_id, relation, weight, now),
            )
            conn.commit()
            return row.lastrowid
        except Exception:
            return None

    def search(self, query: str, memory_type: str = "",
                user_id: str = "", limit: int = 10) -> List[dict]:
        results = self._ltm.search_active(query, limit, memory_type, user_id)
        for r in results:
            self._ltm.reconsolidate(r["id"], is_boost=False)
        return results

    def sleep_cycle(self, max_episodes: int = 100) -> dict:
        now_iso = datetime.now(timezone.utc).isoformat()
        conn = self._store._get_conn()

        last_run = conn.execute(
            "SELECT value FROM _sleep_watermark WHERE key='last_sleep_run'"
        ).fetchone()
        since = last_run["value"] if last_run else (
            datetime.now(timezone.utc) - timedelta(days=1)).isoformat()

        episodes = conn.execute(
            """SELECT id, value, type_data, source_user_id, source_message_ts,
               source_context, created_at
               FROM long_term_entries
               WHERE memory_type='episodic' AND active=1 AND created_at > ?
               ORDER BY created_at LIMIT ?""",
            (datetime.fromisoformat(since).timestamp(), max_episodes),
        ).fetchall()

        if not episodes:
            conn.execute(
                "INSERT OR REPLACE INTO _sleep_watermark (key, value) VALUES ('last_sleep_run', ?)",
                (now_iso,),
            )
            conn.commit()
            return {"status": "no_new_episodes", "since": since}

        stats = {"episodes_scanned": len(episodes), "corrections": 0,
                  "semantic_created": 0, "clusters": 0}

        from .long_term import is_correction_message
        corrections = []
        clean_episodes = []

        for ep in episodes:
            td = json.loads(ep["type_data"] or "{}")
            if td.get("contains_correction") or is_correction_message(ep["value"] or ""):
                target = self._find_correction_target(ep)
                if target:
                    new_value = self._extract_correction_value(ep["value"] or "")
                    if new_value:
                        corrections.append({
                            "target_id": target["id"],
                            "new_value": new_value,
                            "correction_ts": ep["source_message_ts"],
                        })
                    else:
                        td["needs_review"] = True
                        conn.execute(
                            "UPDATE long_term_entries SET type_data=? WHERE id=?",
                            (json.dumps(td, ensure_ascii=False), ep["id"]),
                        )
                else:
                    td["needs_review"] = True
                    conn.execute(
                        "UPDATE long_term_entries SET type_data=? WHERE id=?",
                        (json.dumps(td, ensure_ascii=False), ep["id"]),
                    )
            else:
                clean_episodes.append(ep)

        for c in corrections:
            self.correct(c["target_id"], c["new_value"],
                          correction_ts=c["correction_ts"])
            stats["corrections"] += 1

        if clean_episodes:
            clusters = self._cluster_episodes(clean_episodes)
            stats["clusters"] = len(clusters)

            for cluster in clusters:
                if len(cluster) < 2:
                    continue
                sem_facts = self._abstract_cluster(cluster)
                for fact in sem_facts:
                    sem_id = self._ltm.add_fact(
                        category=fact.get("subcategory", "general"),
                        key=fact.get("key", f"distilled_{int(time.time())}"),
                        value=fact["value"],
                        confidence=fact.get("confidence", 0.4),
                        derivation="distilled",
                        memory_type="semantic",
                        type_data={
                            "subcategory": fact.get("subcategory", "general"),
                            "key": fact.get("key", ""),
                            "resolved_dates": fact.get("resolved_dates", []),
                        },
                        salience=fact.get("salience", 0.4),
                        source_user_id=cluster[0].get("source_user_id", ""),
                        source_message_ts=cluster[0].get("source_message_ts", ""),
                        source_context=cluster[0].get("source_context", ""),
                    )
                    if sem_id:
                        stats["semantic_created"] += 1
                        for ep in cluster:
                            try:
                                conn.execute(
                                    """INSERT OR IGNORE INTO memory_edges
                                       (src_id, dst_id, relation, weight, created_at)
                                       VALUES (?, ?, 'abstracts_from', 0.8, ?)""",
                                    (sem_id, ep["id"], now_iso),
                                )
                            except Exception:
                                pass

        conn.execute(
            "INSERT OR REPLACE INTO _sleep_watermark (key, value) VALUES ('last_sleep_run', ?)",
            (now_iso,),
        )
        conn.commit()
        return stats

    def _find_correction_target(self, episode) -> Optional[dict]:
        value = episode["value"] or ""
        user_id = episode["source_user_id"] or ""
        if not user_id:
            return None
        results = self._ltm.search_active(
            query="", limit=5, memory_type="semantic", user_id=user_id,
        )
        if results:
            return results[0]
        return None

    def _extract_correction_value(self, episode_value: str) -> str:
        import re as _re
        m = _re.search(r'(?:其实是|应该是|是|已经|早就)\s*([^。！？,，\n]{5,50})', episode_value)
        if m:
            return m.group(1)
        m = _re.search(r'不是.*?[，,]\s*(.{5,50})', episode_value)
        if m:
            return m.group(1)
        return ""

    def _cluster_episodes(self, episodes: list) -> List[list]:
        if len(episodes) <= 1:
            return [episodes] if episodes else []
        clusters = []
        remaining = list(episodes)
        while remaining:
            cluster = [remaining.pop(0)]
            i = 0
            while i < len(remaining):
                score = self._episode_similarity(cluster[0], remaining[i])
                if score > 0.3:
                    cluster.append(remaining.pop(i))
                else:
                    i += 1
            clusters.append(cluster)
        return clusters

    def _episode_similarity(self, a, b) -> float:
        a_val = (a.get("value") or "").lower()
        b_val = (b.get("value") or "").lower()
        from .long_term import LongTermMemory
        a_chars = set(a_val)
        b_chars = set(b_val)
        if not a_chars or not b_chars:
            return 0.0
        return len(a_chars & b_chars) / max(len(a_chars), len(b_chars))

    def _abstract_cluster(self, cluster: list) -> List[dict]:
        import requests as _r
        texts = "\n".join(
            f"- {e.get('source_message_ts','')}: {e.get('value','')[:200]}"
            for e in cluster
        )
        today = datetime.now(timezone.utc).strftime("%Y年%m月%d日")
        prompt = (
            f"当前日期: {today}。所有相对日期转为绝对日期。\n"
            f"从以下群聊片段提取稳定的语义事实。只提取确定的事实。\n"
            f"片段:\n{texts}\n\n"
            f"输出 JSON: {{\"facts\":[{{\"value\":\"...\",\"subcategory\":\"user_profile|user_preference|knowledge|decision|relationship\",\"salience\":0.5,\"confidence\":0.5,\"resolved_dates\":[]}}]}}"
        )
        try:
            import os as _os
            api_key = _os.getenv("HERMES_API_KEY", "")
            r = _r.post(
                "https://api.deepseek.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": "deepseek-v4-flash",
                    "messages": [
                        {"role": "system", "content": "你是一个记忆蒸馏器。提取事实。只输出JSON。"},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 2000,
                    "temperature": 0.3,
                },
                timeout=30,
            )
            data = r.json()
            raw = data["choices"][0]["message"]["content"].strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
            result = json.loads(raw)
            return result.get("facts", [])
        except Exception as e:
            logger.warning("Abstract cluster failed: %s", e)
            return []

    def source_conflict_scan(self, user_id: str = "",
                              dry_run: bool = True) -> List[dict]:
        results = self._ltm.search_active(
            query="", limit=50, memory_type="semantic", user_id=user_id,
        )
        conflicts = []
        for i, a in enumerate(results):
            for b in results[i + 1:]:
                if a.get("subcategory") != b.get("subcategory"):
                    continue
                a_vals = set((a["value"] or "").lower().split())
                b_vals = set((b["value"] or "").lower().split())
                overlap = len(a_vals & b_vals)
                if overlap > 1 and overlap / max(len(a_vals), len(b_vals)) > 0.4:
                    conflicts.append({
                        "a": a["id"], "a_value": a["value"],
                        "b": b["id"], "b_value": b["value"],
                        "subcategory": a["subcategory"],
                    })
        return conflicts

    def shutdown(self):
        """Clean shutdown."""
        self._store.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.shutdown()
