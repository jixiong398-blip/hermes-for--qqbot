"""Focused regression tests for the local TurnContext API-copy seam."""

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


def test_run_conversation_projects_historical_sidecar_without_mutating_transcript():
    agent = _agent()
    agent.client.chat.completions.create.return_value = _response()
    history = [
        {
            "role": "user",
            "content": "canonical transcript",
            "api_content": "provider-only projection",
            "timestamp": 123.5,
            "display_kind": "internal_notification",
            "display_metadata": {"visible": False},
            "metadata": {"nested": ["canonical"]},
        },
        {"role": "assistant", "content": "prior answer"},
    ]

    with (
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation(
            "follow-up",
            conversation_history=history,
        )

    assert result["completed"] is True
    sent_messages = agent.client.chat.completions.create.call_args.kwargs["messages"]
    projected = next(
        message
        for message in sent_messages
        if message.get("content") == "provider-only projection"
    )
    assert "api_content" not in projected
    assert "timestamp" not in projected
    assert "display_kind" not in projected
    assert "display_metadata" not in projected
    assert history[0]["content"] == "canonical transcript"
    assert history[0]["api_content"] == "provider-only projection"
    assert history[0]["timestamp"] == 123.5
    assert history[0]["display_kind"] == "internal_notification"
    assert history[0]["display_metadata"] == {"visible": False}
    assert history[0]["metadata"] == {"nested": ["canonical"]}


def test_sessiondb_writer_does_not_receive_sidecar_or_ephemeral_content():
    agent = _agent()
    agent.session_id = "session-test"
    agent._session_db_created = True
    agent._last_flushed_db_idx = 0
    agent._session_db.append_message = MagicMock()
    messages = [
        {
            "role": "user",
            "content": "clean",
            "api_content": "ephemeral provider copy",
        }
    ]

    agent._flush_messages_to_session_db(messages, [])

    kwargs = agent._session_db.append_message.call_args.kwargs
    assert kwargs["content"] == "clean"
    assert "api_content" not in kwargs


def test_clone_message_for_api_keeps_empty_user_content_empty_without_sidecar():
    message = {"role": "user", "content": ""}

    projected = clone_message_for_api(message)

    assert projected == message
    assert projected is not message


def test_canonical_rewrites_drop_stale_api_sidecar():
    agent = _agent()
    agent._persist_user_message_idx = 0
    agent._persist_user_message_override = "rewritten"
    overridden = [
        {"role": "user", "content": "original", "api_content": "old-wire"}
    ]
    agent._apply_persist_user_message_override(overridden)
    assert overridden[0]["content"] == "rewritten"
    assert "api_content" not in overridden[0]

    merged = [
        {"role": "user", "content": "first", "api_content": "first-wire"},
        {"role": "user", "content": "second", "api_content": "second-wire"},
    ]
    assert agent._repair_message_sequence(merged) == 1
    assert merged[0]["content"] == "first\n\nsecond"
    assert "api_content" not in merged[0]
