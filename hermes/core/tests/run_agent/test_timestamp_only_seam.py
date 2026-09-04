"""Focused regressions for the optional platform timestamp handoff."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from agent.turn_context_contract import clone_message_for_api
from run_agent import AIAgent


def _response(content: str = "done"):
    message = SimpleNamespace(
        content=content,
        tool_calls=None,
        reasoning=None,
        reasoning_content=None,
        reasoning_details=None,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        model="test/model",
        usage=None,
    )


def _agent():
    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            session_db=MagicMock(),
        )
    agent.client = MagicMock()
    agent._cached_system_prompt = "stable system"
    agent.compression_enabled = False
    return agent


def test_supplied_timestamp_is_stamped_but_removed_from_provider_copy():
    agent = _agent()
    agent.client.chat.completions.create.return_value = _response()
    captured = []

    with (
        patch.object(agent, "_persist_session", side_effect=lambda messages, *_: captured.extend(messages)),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation(
            "hello",
            persist_user_timestamp=123.5,
        )

    assert result["completed"] is True
    user_row = next(row for row in captured if row.get("role") == "user")
    assert user_row["timestamp"] == 123.5
    sent = agent.client.chat.completions.create.call_args.kwargs["messages"]
    assert all("timestamp" not in row for row in sent)
    assert clone_message_for_api(user_row).get("timestamp") is None


def test_default_timestamp_is_local_and_legacy_writer_shape_is_preserved():
    agent = _agent()
    agent.session_id = "session-test"
    agent._session_db_created = True
    agent._last_flushed_db_idx = 0
    agent._session_db.append_message = MagicMock()

    messages = [{"role": "user", "content": "clean", "timestamp": 42.0}]
    agent._flush_messages_to_session_db(messages, [])

    kwargs = agent._session_db.append_message.call_args.kwargs
    assert kwargs["content"] == "clean"
    assert kwargs["timestamp"] == 42.0

    class LegacyWriter:
        def __init__(self):
            self.calls = []

        def append_message(
            self,
            session_id,
            role,
            content,
            tool_name=None,
            tool_calls=None,
            tool_call_id=None,
            token_count=None,
            finish_reason=None,
            reasoning=None,
            reasoning_content=None,
            reasoning_details=None,
            codex_reasoning_items=None,
            codex_message_items=None,
        ):
            self.calls.append((session_id, role, content))

    legacy = LegacyWriter()
    agent._session_db = legacy
    agent._last_flushed_db_idx = 0
    agent._flush_messages_to_session_db(messages, [])
    assert legacy.calls == [("session-test", "user", "clean")]


def test_invalid_event_timestamp_falls_back_without_reaching_agent_metadata():
    from gateway.run import _event_timestamp_seconds
    from plugins.platforms.onebot.adapter import _onebot_event_timestamp

    assert _event_timestamp_seconds(SimpleNamespace(timestamp=None)) is None
    assert _event_timestamp_seconds(SimpleNamespace(timestamp=float("nan"))) is None
    assert _event_timestamp_seconds(SimpleNamespace(timestamp=-1)) is None
    assert _event_timestamp_seconds(SimpleNamespace(timestamp=123.5)) == 123.5
    event_time = _onebot_event_timestamp({"time": 1760000000})
    assert event_time is not None
    assert event_time.year == 2025
    assert _onebot_event_timestamp({"time": "not-a-time"}) is None
