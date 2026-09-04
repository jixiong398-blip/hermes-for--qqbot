"""Contract tests for the opt-in UnifiedMemoryGateway provider adapter."""

from typing import Any, Dict, List

from agent.builtin_memory_provider import BuiltinMemoryProviderAdapter
from agent.memory_provider import RecallStatus


class _FakeGateway:
    def __init__(self) -> None:
        self.recall_calls = []
        self.turn_calls = []
        self.end_calls = []

    def build_memory_prompt_block(self, max_chars: int = 3000) -> str:
        return "## Long-term memory\n- user prefers concise answers"

    def recall_structured(self, query: str, session_id=None, max_chars: int = 4000) -> dict:
        self.recall_calls.append((query, session_id, max_chars))
        return {"prompt": "## Recall\n- prefers concise answers", "results": ["hit"]}

    def get_stm_context(self, session_id: str, chat_type: str = "dm") -> str:
        return f"## STM ({chat_type})\n- recent context"

    def process_turn(self, **kwargs: Any) -> None:
        self.turn_calls.append(kwargs)

    def on_session_end(self, session_id: str) -> None:
        self.end_calls.append(session_id)


def test_adapter_is_lazy_and_does_not_create_gateway_during_availability_check():
    adapter = BuiltinMemoryProviderAdapter()
    assert adapter.name == "builtin"
    assert adapter._gateway is None
    assert adapter.is_available() is True
    assert adapter._gateway is None


def test_prefetch_skips_trivial_prompts_and_surfaces_recall_status():
    gateway = _FakeGateway()
    adapter = BuiltinMemoryProviderAdapter(gateway)
    adapter.initialize("session-1", chat_type="group")

    assert adapter.prefetch("ok", session_id="session-1") == ""
    assert gateway.recall_calls == []
    assert adapter.recall_status() is None

    result = adapter.prefetch("what do I prefer?", session_id="session-1")
    assert "## STM (group)" in result
    assert "## Recall" in result
    assert gateway.recall_calls == [("what do I prefer?", "session-1", 4000)]
    assert adapter.recall_status() == RecallStatus("Unified Memory", 1)


def test_sync_turn_maps_one_completed_exchange_to_gateway():
    gateway = _FakeGateway()
    adapter = BuiltinMemoryProviderAdapter(gateway)
    adapter.initialize(
        "session-1",
        chat_type="group",
        user_id="user-7",
        user_name="Member",
        bot_name="Soyo",
    )

    adapter.sync_turn(
        "remember this preference",
        "I will remember it.",
        session_id="session-1",
        messages=[{"role": "tool", "content": "ignored execution detail"}],
    )

    assert [call["role"] for call in gateway.turn_calls] == ["user", "assistant"]
    assert gateway.turn_calls[0]["speaker_name"] == "Member"
    assert gateway.turn_calls[0]["chat_type"] == "group"
    assert gateway.turn_calls[0]["bot_replied"] is True
    assert gateway.turn_calls[1]["speaker_name"] == "Soyo"


def test_session_switch_and_end_reset_adapter_scoped_state():
    gateway = _FakeGateway()
    adapter = BuiltinMemoryProviderAdapter(gateway)
    adapter.initialize("old-session")
    adapter._last_recall_status = RecallStatus("Unified Memory", 2)

    adapter.on_session_end([])
    assert gateway.end_calls == ["old-session"]
    assert adapter.recall_status() is None

    adapter.on_session_switch("new-session", parent_session_id="old-session", reset=True)
    assert adapter._session_id == "new-session"
    assert adapter.recall_status() is None


def test_real_gateway_adapter_writes_stm_without_network_or_duplicate_tool_surface(tmp_path):
    from agent.memory.gateway import UnifiedMemoryGateway

    gateway = UnifiedMemoryGateway(
        db_path=tmp_path / "memory.db",
        enable_wiki=False,
        enable_episodes=False,
    )
    adapter = BuiltinMemoryProviderAdapter(gateway)
    adapter.initialize("session-real", chat_type="dm", user_name="Member")
    adapter.sync_turn("a durable user turn", "a durable assistant reply")

    recent = gateway._stm.get_recent("session-real", n=10)
    assert [entry.role for entry in recent] == ["user", "assistant"]
    assert [entry.content for entry in recent] == [
        "a durable user turn",
        "a durable assistant reply",
    ]
    assert adapter.get_tool_schemas() == []
    gateway.shutdown()
