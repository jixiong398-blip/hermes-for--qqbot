"""Disposable privacy and lifecycle regressions for the local memory stack."""

from __future__ import annotations

import asyncio
import json

import pytest

from agent.memory import event_stream
from agent.memory.gateway import UnifiedMemoryGateway
from agent.memory.store import MemoryStore
from agent.builtin_memory_provider import BuiltinMemoryProviderAdapter
from agent.memory_manager import MemoryManager
from gateway.builtin_hooks import memory_maintenance
from hermes_state import SessionDB
from run_agent import AIAgent


@pytest.fixture
def scoped_memory(tmp_path, monkeypatch):
    stream_path = tmp_path / "layer0" / "events.jsonl"
    monkeypatch.setattr(event_stream, "_STREAM_PATH", stream_path)
    monkeypatch.setattr(event_stream, "_EVENT_COUNTER", 0)
    gateway = UnifiedMemoryGateway(
        db_path=tmp_path / "memory.db",
        wiki_dirs=[],
        enable_wiki=False,
        enable_episodes=True,
        episode_reveal_names=False,
    )
    monkeypatch.setattr(memory_maintenance, "_MEMORY_GW", gateway)
    try:
        yield gateway, stream_path
    finally:
        gateway.shutdown()


def _emit(event_type: str, context: dict) -> None:
    asyncio.run(memory_maintenance.handle(event_type, context))


def _turns(gateway, session_id: str, chat_type: str, marker: str, speaker: str):
    """Create enough bounded rows for one EPI fragment."""

    for index in range(3):
        gateway.process_turn(
            session_id,
            "user",
            f"{marker} project note iteration {index} uses SQLite",
            speaker_name=speaker,
            chat_type=chat_type,
        )
        gateway.process_turn(
            session_id,
            "assistant",
            f"Acknowledged the {marker} project note iteration {index}.",
            speaker_name="Soyo",
            chat_type=chat_type,
        )


def test_opaque_chat_ids_use_explicit_scope_in_stm_and_layer0(scoped_memory):
    gateway, stream_path = scoped_memory
    first_session = "onebot:group:opaque-alpha"
    second_session = "onebot:group:opaque-beta"

    _emit(
        "agent:start",
        {
            "session_id": first_session,
            "message": "alpha-only context",
            "platform": "onebot",
            "user_id": "same-user",
            "user_name": "Member Alpha",
            "chat_id": "opaque-alpha",
            "chat_type": "group",
            "thread_id": "thread-alpha",
        },
    )
    _emit(
        "agent:start",
        {
            "session_id": second_session,
            "message": "beta-only context",
            "platform": "onebot",
            "user_id": "same-user",
            "user_name": "Member Beta",
            "chat_id": "opaque-beta",
            "chat_type": "group",
            "thread_id": "thread-beta",
        },
    )

    first_context = gateway.get_stm_context(first_session, chat_type="group")
    second_context = gateway.get_stm_context(second_session, chat_type="group")
    assert "alpha-only context" in first_context
    assert "beta-only context" not in first_context
    assert "beta-only context" in second_context
    assert "alpha-only context" not in second_context

    events = event_stream.read_events(stream_path=stream_path, event_types=["message"])
    assert len(events) == 2
    by_session = {event["session_id"]: event for event in events}
    assert by_session[first_session]["chat_id"] == "opaque-alpha"
    assert by_session[first_session]["chat_type"] == "group"
    assert by_session[first_session]["thread_id"] == "thread-alpha"
    assert by_session[second_session]["chat_id"] == "opaque-beta"
    assert by_session[second_session]["chat_type"] == "group"
    assert by_session[second_session]["thread_id"] == "thread-beta"


def test_gateway_hook_defers_stm_until_completed_turn(scoped_memory):
    gateway, stream_path = scoped_memory
    interrupted_session = "onebot:group:opaque-interrupted"
    completed_session = "onebot:group:opaque-completed"

    interrupted_context = {
        "session_id": interrupted_session,
        "message": "interrupted private context",
        "response": "partial response",
        "platform": "onebot",
        "user_id": "same-user",
        "user_name": "Member",
        "chat_id": "opaque-interrupted",
        "chat_type": "group",
        "_defer_memory_until_end": True,
    }
    _emit("agent:start", interrupted_context)
    assert gateway._stm.get_recent(interrupted_session, n=10) == []

    _emit(
        "agent:end",
        {
            **interrupted_context,
            "completed": False,
            "interrupted": True,
            "failed": False,
        },
    )
    assert gateway._stm.get_recent(interrupted_session, n=10) == []

    failed_session = "onebot:group:opaque-failed"
    failed_context = {
        **interrupted_context,
        "session_id": failed_session,
        "message": "failed context",
        "response": "request failed",
        "chat_id": "opaque-failed",
    }
    _emit("agent:start", failed_context)
    _emit(
        "agent:end",
        {
            **failed_context,
            "completed": False,
            "interrupted": False,
            "failed": True,
        },
    )
    assert gateway._stm.get_recent(failed_session, n=10) == []

    completed_context = {
        "session_id": completed_session,
        "message": "completed context",
        "response": "completed response",
        "platform": "onebot",
        "user_id": "same-user",
        "user_name": "Member",
        "chat_id": "opaque-completed",
        "chat_type": "group",
        "_defer_memory_until_end": True,
    }
    _emit("agent:start", completed_context)
    _emit(
        "agent:end",
        {
            **completed_context,
            "completed": True,
            "interrupted": False,
            "failed": False,
        },
    )
    completed_rows = gateway._stm.get_recent(completed_session, n=10)
    assert [(row.role, row.content) for row in completed_rows] == [
        ("user", "completed context"),
        ("assistant", "completed response"),
    ]

    events = event_stream.read_events(stream_path=stream_path, event_types=["message"])
    by_session = {}
    for event in events:
        by_session.setdefault(event["session_id"], []).append(event)
    assert len(by_session[interrupted_session]) == 1
    assert {event["role"] for event in by_session[completed_session]} == {
        "user",
        "assistant",
    }


def test_explicit_chat_type_blocks_mixed_legacy_session_recall(scoped_memory):
    gateway, _ = scoped_memory
    shared_session = "onebot:shared:opaque"

    gateway.process_turn(
        shared_session,
        "user",
        "group-only memory marker",
        chat_type="group",
    )
    gateway.process_turn(
        shared_session,
        "user",
        "dm-only memory marker",
        chat_type="dm",
    )

    group_context = gateway.get_stm_context(shared_session, chat_type="group")
    dm_context = gateway.get_stm_context(shared_session, chat_type="dm")
    assert "group-only memory marker" in group_context
    assert "dm-only memory marker" not in group_context
    assert "dm-only memory marker" in dm_context
    assert "group-only memory marker" not in dm_context

    group_results = gateway._retriever.recall(
        "group-only memory marker",
        shared_session,
        include_sources=["short_term"],
        chat_type="group",
    )
    dm_results = gateway._retriever.recall(
        "dm-only memory marker",
        shared_session,
        include_sources=["short_term"],
        chat_type="dm",
    )
    assert len(group_results) == 1
    assert "dm-only" not in group_results[0].content
    assert len(dm_results) == 1
    assert "group-only" not in dm_results[0].content


def test_same_user_cross_chat_epi_is_anonymous_and_dm_is_not_shown_to_group(
    scoped_memory,
):
    gateway, _ = scoped_memory
    group_a = "onebot:group:opaque-alpha"
    group_b = "onebot:group:opaque-beta"
    private_dm = "onebot:dm:opaque-user"

    _turns(gateway, group_a, "group", "shared Aurora", "Same User")
    _turns(gateway, private_dm, "dm", "private medical", "Same User")

    group_entries = gateway._stm.get_recent(group_a, n=20)
    dm_entries = gateway._stm.get_recent(private_dm, n=20)
    assert gateway._epi.index_session(group_a, "group", group_entries) == 1
    assert gateway._epi.index_session(private_dm, "dm", dm_entries) == 1

    group_hits = gateway.recall_episodes(
        "shared Aurora project",
        session_id=group_b,
        chat_type="group",
        limit=10,
    )
    assert group_hits
    rendered = "\n".join(item["text"] for item in group_hits)
    assert "shared Aurora" in rendered
    assert "Same User" not in rendered
    assert "opaque-alpha" not in rendered
    assert "opaque-user" not in rendered

    private_hits = gateway.recall_episodes(
        "private medical project",
        session_id=group_b,
        chat_type="group",
        limit=10,
    )
    assert private_hits == []

    adapter = BuiltinMemoryProviderAdapter(gateway)
    adapter.initialize(group_b, chat_type="group", user_id="same-user")
    assert "private medical" not in adapter.prefetch(
        "private medical project",
        session_id=group_b,
    )


def test_chat_buffer_scope_isolated_when_opaque_id_is_reused(tmp_path):
    store = MemoryStore(tmp_path / "buffer.db")
    try:
        store.add_chat_buffer("opaque-chat", "group", "member", "group row")
        store.add_chat_buffer("opaque-chat", "dm", "member", "dm row")

        group_rows = store.get_chat_buffer("opaque-chat", chat_type="group")
        dm_rows = store.get_chat_buffer("opaque-chat", chat_type="dm")
        assert [row["text"] for row in group_rows] == ["group row"]
        assert [row["text"] for row in dm_rows] == ["dm row"]
    finally:
        store.close()


def test_completed_memory_and_interrupted_memory_match_sessiondb_lineage(
    scoped_memory,
    tmp_path,
):
    gateway, _ = scoped_memory
    root_session = "onebot:group:completed-root"
    child_session = "onebot:group:compression-child"
    completed_user = "completed turn stays in memory"
    interrupted_user = "interrupted turn must not enter memory"

    provider = BuiltinMemoryProviderAdapter(gateway)
    provider.initialize(
        root_session,
        chat_type="group",
        user_id="same-user",
        user_name="Member",
    )
    manager = MemoryManager()
    manager.add_provider(provider)

    holder = AIAgent.__new__(AIAgent)
    holder._memory_manager = manager
    holder.session_id = root_session
    holder._suppress_session_persistence = False

    AIAgent._sync_external_memory_for_turn(
        holder,
        original_user_message=completed_user,
        final_response="completed assistant response",
        interrupted=False,
        messages=[
            {"role": "user", "content": completed_user},
            {"role": "assistant", "content": "completed assistant response"},
        ],
    )
    AIAgent._sync_external_memory_for_turn(
        holder,
        original_user_message=interrupted_user,
        final_response="partial response",
        interrupted=True,
        messages=[{"role": "user", "content": interrupted_user}],
    )

    memory_rows = gateway._stm.get_recent(root_session, n=20)
    assert [row.content for row in memory_rows] == [
        completed_user,
        "completed assistant response",
    ]

    session_db = SessionDB(tmp_path / "transcript.db")
    try:
        session_db.create_session(root_session, source="onebot")
        session_db.append_message(root_session, "user", completed_user, timestamp=100.0)
        session_db.append_message(
            root_session,
            "assistant",
            "completed assistant response",
            timestamp=101.0,
        )
        session_db.end_session(root_session, "compression")
        session_db.create_session(
            child_session,
            source="onebot",
            parent_session_id=root_session,
        )
        session_db.append_message(child_session, "user", interrupted_user, timestamp=90.0)

        transcript = session_db.get_messages_as_conversation(
            child_session,
            include_ancestors=True,
        )
        assert [row["content"] for row in transcript] == [
            interrupted_user,
            completed_user,
            "completed assistant response",
        ]
        assert session_db.get_session(child_session)["parent_session_id"] == root_session
        assert gateway._stm.get_recent(child_session, n=20) == []
        assert interrupted_user not in json.dumps(
            [{"content": row.content} for row in memory_rows],
            ensure_ascii=False,
        )
    finally:
        session_db.close()
