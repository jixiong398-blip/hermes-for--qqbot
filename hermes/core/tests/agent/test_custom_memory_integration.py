"""Real local integration coverage for the QQ memory stack.

These tests deliberately avoid ``MagicMock`` for the custom memory backend.
They exercise the built-in lifecycle hook, the unified SQLite store, STM/EPI/LTM
promotion, Layer 0 JSONL, and reload/recall from a fresh gateway instance.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from agent.memory import event_stream
from agent.memory.gateway import UnifiedMemoryGateway
from gateway.builtin_hooks import memory_maintenance


@pytest.fixture
def custom_memory_gateway(tmp_path, monkeypatch):
    stream_path = tmp_path / "data" / "layer0.jsonl"
    monkeypatch.setattr(event_stream, "_STREAM_PATH", stream_path)
    monkeypatch.setattr(event_stream, "_EVENT_COUNTER", 0)

    gateway = UnifiedMemoryGateway(
        db_path=tmp_path / "memory.db",
        wiki_dirs=[],
        enable_wiki=False,
        enable_episodes=True,
        consolidation_min_turns=6,
    )
    monkeypatch.setattr(memory_maintenance, "_MEMORY_GW", gateway)
    try:
        yield gateway, stream_path
    finally:
        gateway.shutdown()


def test_builtin_memory_hook_records_real_stm_and_layer0(custom_memory_gateway):
    gateway, stream_path = custom_memory_gateway
    session_id = "onebot:group:memory-fixture"

    asyncio.run(
        memory_maintenance.handle(
            "agent:start",
            {
                "session_id": session_id,
                "message": "我喜欢使用 Vim 编辑器",
                "platform": "onebot",
                "user_id": "fixture-user",
                "user_name": "Fixture User",
                "chat_id": "opaque-chat-id",
                "chat_type": "group",
            },
        )
    )
    asyncio.run(
        memory_maintenance.handle(
            "agent:end",
            {
                "session_id": session_id,
                "response": "记住了你的编辑器偏好。",
                "platform": "onebot",
                "user_id": "fixture-user",
                "chat_id": "opaque-chat-id",
                "chat_type": "group",
                "bot_name": "Soyo",
            },
        )
    )

    recent = gateway._stm.get_recent(session_id, n=10, chat_type="group")
    assert [(entry.role, entry.content, entry.chat_type) for entry in recent] == [
        ("user", "我喜欢使用 Vim 编辑器", "group"),
        ("assistant", "记住了你的编辑器偏好。", "group"),
    ]

    events = event_stream.read_events(
        stream_path=stream_path,
        event_types=["message"],
        limit=10,
    )
    assert len(events) == 2
    assert {event["role"] for event in events} == {"user", "assistant"}
    assert {event["session_id"] for event in events} == {session_id}
    assert all(event["platform"] == "onebot" for event in events)


def test_real_gateway_consolidates_stm_to_ltm_and_epi_then_reloads(
    custom_memory_gateway,
    tmp_path,
    monkeypatch,
):
    gateway, stream_path = custom_memory_gateway
    session_id = "onebot:group:promotion-fixture"
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)

    for index in range(3):
        gateway.process_turn(
            session_id,
            "user",
            "我喜欢使用 Vim 编辑器",
            speaker_name="fixture-user",
            chat_type="group",
        )
        gateway.process_turn(
            session_id,
            "assistant",
            f"第 {index + 1} 次确认这个编辑器偏好。",
            speaker_name="Soyo",
            chat_type="group",
        )

    stats = gateway.consolidate(session_id)
    assert stats["status"] == "completed"
    assert stats["extraction_method"] == "regex"
    assert stats["facts_promoted"] >= 1
    assert stats.get("episodes_indexed", 0) >= 1

    ltm_hits = gateway._ltm.search("Vim", limit=10)
    assert any("Vim" in entry.value for entry in ltm_hits)
    episode_hits = gateway.recall_episodes(
        "Vim 编辑器",
        session_id="onebot:dm:another-session",
        limit=10,
    )
    assert episode_hits
    assert any("Vim" in item["text"] for item in episode_hits)

    memory_db = tmp_path / "memory.db"
    gateway.shutdown()
    reloaded = UnifiedMemoryGateway(
        db_path=memory_db,
        wiki_dirs=[],
        enable_wiki=False,
        enable_episodes=True,
    )
    try:
        reloaded_hits = reloaded._ltm.search("Vim", limit=10)
        assert any("Vim" in entry.value for entry in reloaded_hits)
        context = reloaded.get_context_for_agent(
            "Vim 编辑器",
            session_id="onebot:dm:another-session",
            chat_type="dm",
        )
        assert "Vim" in context
    finally:
        reloaded.shutdown()


def test_custom_memory_fixture_is_jsonl_and_not_provider_or_transcript_data(
    custom_memory_gateway,
):
    gateway, stream_path = custom_memory_gateway
    gateway.process_turn(
        "onebot:dm:shape-fixture",
        "user",
        "请记住这个本地偏好",
        speaker_name="fixture-user",
        chat_type="dm",
    )

    assert not stream_path.exists()
    # The direct gateway API owns STM; Layer 0 is deliberately written by the
    # lifecycle hook, so a direct process_turn must not silently duplicate it.
    recent = gateway._stm.get_recent("onebot:dm:shape-fixture", n=5)
    assert len(recent) == 1
    assert json.loads(json.dumps(recent[0].content, ensure_ascii=False)) == (
        "请记住这个本地偏好"
    )


def test_v1_memory_store_copy_receives_additive_ltm_schema_without_data_loss(tmp_path):
    """Old local memory DBs get the fields that the closed-loop code reads."""

    database_path = tmp_path / "legacy-memory.db"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE long_term_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL DEFAULT 'general',
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            tags TEXT DEFAULT '[]',
            confidence REAL DEFAULT 0.5,
            source_session_ids TEXT DEFAULT '[]',
            retrieval_count INTEGER DEFAULT 0,
            last_retrieved REAL DEFAULT 0.0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            UNIQUE(category, key)
        );
        CREATE TABLE chat_message_buffer (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT NOT NULL,
            chat_type TEXT NOT NULL DEFAULT 'group',
            user_id INTEGER,
            sender_name TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            is_bot INTEGER DEFAULT 0,
            created_at REAL NOT NULL
        );
        INSERT INTO long_term_entries
            (category, key, value, created_at, updated_at)
        VALUES ('user_preferences', 'editor', '用户偏好 Vim', 1.0, 1.0);
        """
    )
    connection.commit()
    connection.close()

    from agent.memory.store import MemoryStore

    store = MemoryStore(database_path)
    try:
        columns = {
            row[1]
            for row in store._get_conn().execute(
                "PRAGMA table_info(long_term_entries)"
            )
        }
        assert {
            "memory_type",
            "type_data",
            "salience",
            "recall_strength",
            "reconsolidation_count",
            "source_user_id",
            "source_context",
            "active",
            "deleted_at",
        } <= columns
        chat_columns = {
            row[1]
            for row in store._get_conn().execute(
                "PRAGMA table_info(chat_message_buffer)"
            )
        }
        assert "message_id" in chat_columns
        assert store._get_conn().execute(
            "SELECT value FROM long_term_entries WHERE key = 'editor'"
        ).fetchone()[0] == "用户偏好 Vim"
        assert store._get_conn().execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'memory_edges'"
        ).fetchone() is not None
        assert store._get_conn().execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = '_sleep_watermark'"
        ).fetchone() is not None
        new_id = store.supersede_memory(1, "用户偏好 Neovim", 0.9)
        assert new_id > 1
        rows = store._get_conn().execute(
            "SELECT id, key, value, active, supersedes_id "
            "FROM long_term_entries ORDER BY id"
        ).fetchall()
        assert [(row[1], row[2], bool(row[3])) for row in rows] == [
            ("editor__superseded_1", "用户偏好 Vim", False),
            ("editor", "用户偏好 Neovim", True),
        ]
        assert rows[1][4] == 1
        assert [entry.value for entry in store.search_long_term("Neovim")] == [
            "用户偏好 Neovim"
        ]
        assert len(store.get_long_term(include_inactive=True)) == 2
    finally:
        store.close()


def test_real_gateway_workflow_and_core_memory_layers_are_persistent(tmp_path):
    from agent.memory.gateway import UnifiedMemoryGateway

    memory_db = tmp_path / "workflow-memory.db"
    gateway = UnifiedMemoryGateway(
        db_path=memory_db,
        wiki_dirs=[],
        enable_wiki=False,
        enable_episodes=False,
    )
    try:
        workflow_id = gateway.add_workflow(
            "deploy-fixture",
            "验证部署流程",
            ["运行测试", "检查日志"],
            trigger_patterns=["部署", "发布"],
        )
        assert workflow_id > 0
        assert gateway.search_workflows("部署")[0]["name"] == "deploy-fixture"
        gateway.record_workflow_use("deploy-fixture", success=True)
        assert gateway.get_workflow_decay_report()[0]["usage_count"] == 1

        core_id = gateway._store.add_core_memory(
            "agent_identity",
            "这是一个仅用于集成测试的核心记忆",
            source="test",
        )
        assert core_id > 0
        assert "核心记忆" in gateway._store.load_core_memories_prompt()
        assert gateway._store.soft_delete_core_memory(core_id) is True
        assert gateway._store.load_core_memories_prompt() == ""
    finally:
        gateway.shutdown()


def test_run_agent_syncs_completed_turn_to_real_builtin_memory_provider(tmp_path):
    from agent.builtin_memory_provider import BuiltinMemoryProviderAdapter
    from agent.memory_manager import MemoryManager
    from run_agent import AIAgent

    memory_gateway = UnifiedMemoryGateway(
        db_path=tmp_path / "agent-memory.db",
        wiki_dirs=[],
        enable_wiki=False,
        enable_episodes=False,
    )
    session_id = "onebot:dm:agent-memory-fixture"
    try:
        provider = BuiltinMemoryProviderAdapter(memory_gateway)
        provider.initialize(
            session_id,
            chat_type="dm",
            user_id="fixture-user",
            user_name="Fixture User",
            bot_name="Soyo",
        )
        manager = MemoryManager()
        manager.add_provider(provider)

        agent = object.__new__(AIAgent)
        agent._memory_manager = manager
        agent.session_id = session_id
        agent._suppress_session_persistence = False

        AIAgent._sync_external_memory_for_turn(
            agent,
            original_user_message="我在维护一个本地部署项目",
            final_response="我会记住这个项目背景。",
            interrupted=False,
            messages=[
                {"role": "user", "content": "我在维护一个本地部署项目"},
                {"role": "assistant", "content": "我会记住这个项目背景。"},
            ],
        )

        recent = memory_gateway._stm.get_recent(session_id, n=10, chat_type="dm")
        assert [(entry.role, entry.content) for entry in recent] == [
            ("user", "我在维护一个本地部署项目"),
            ("assistant", "我会记住这个项目背景。"),
        ]
        recalled = provider.prefetch(
            "本地部署项目",
            session_id=session_id,
        )
        assert "本地部署项目" in recalled

        AIAgent._sync_external_memory_for_turn(
            agent,
            original_user_message="这条中断回合不应写入记忆",
            final_response="不完整的回复",
            interrupted=True,
            messages=[],
        )
        recent_after_interrupt = memory_gateway._stm.get_recent(
            session_id,
            n=10,
            chat_type="dm",
        )
        assert all("中断回合" not in entry.content for entry in recent_after_interrupt)
    finally:
        memory_gateway.shutdown()


def test_gateway_hook_context_carries_explicit_group_metadata(monkeypatch):
    from types import SimpleNamespace

    from gateway.config import Platform
    from gateway.run import _build_agent_hook_context
    from gateway.session import SessionSource

    monkeypatch.delenv("ONEBOT_BOT_NAME", raising=False)

    source = SessionSource(
        platform=Platform("onebot"),
        chat_id="opaque-chat-id",
        chat_name="Fixture Group",
        chat_type="group",
        user_id="fixture-user",
        user_name="Fixture User",
        thread_id="topic-1",
    )
    context = _build_agent_hook_context(
        event=SimpleNamespace(message_id="message-1"),
        source=source,
        session_id="onebot:group:fixture",
        message="x" * 600,
    )

    assert context == {
        "platform": "onebot",
        "user_id": "fixture-user",
        "user_name": "Fixture User",
        "chat_id": "opaque-chat-id",
        "chat_name": "Fixture Group",
        "chat_type": "group",
        "thread_id": "topic-1",
        "message_id": "message-1",
        "bot_name": "Soyo",
        "session_id": "onebot:group:fixture",
        "message": "x" * 500,
    }
