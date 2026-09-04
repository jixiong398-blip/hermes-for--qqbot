"""Regression tests for repetition-dominated length continuations."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def loop_agent():
    """Build a minimal agent whose API client is fully controlled by the test."""
    from run_agent import AIAgent

    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
        agent.client = MagicMock()
        agent._cached_system_prompt = "You are helpful."
        agent._use_prompt_caching = False
        agent.compression_enabled = False
        agent.save_trajectories = False
        return agent


def _truncated_response(content: str) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=None)
    choice = SimpleNamespace(message=message, finish_reason="length")
    return SimpleNamespace(
        id="partial-stream-stub",
        model="test/model",
        choices=[choice],
        usage=None,
    )


def _run(agent, message: str):
    with (
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        return agent.run_conversation(message)


class TestContinuationRepetitionGuard:
    def test_repetition_dominated_truncation_stops_before_continuation(self, loop_agent):
        repeated = "The model repeated this complete sentence instead of continuing. " * 200
        loop_agent.client.chat.completions.create.side_effect = [
            _truncated_response(repeated),
        ]

        result = _run(loop_agent, "write a long report")

        assert result["completed"] is False
        assert result["partial"] is True
        assert "Repetition" in result["final_response"]
        assert "repetition loop" in result["error"]
        assert loop_agent.client.chat.completions.create.call_count == 1
        assert not any(
            isinstance(message, dict)
            and message.get("_length_continuation_fragment")
            for message in result["messages"]
        )

    def test_normal_truncation_still_continues(self, loop_agent):
        loop_agent.client.chat.completions.create.side_effect = [
            _truncated_response("part one "),
            _truncated_response("part two "),
            _truncated_response("part three."),
        ]

        result = _run(loop_agent, "write a long report")

        assert result["partial"] is True
        assert loop_agent.client.chat.completions.create.call_count == 3
