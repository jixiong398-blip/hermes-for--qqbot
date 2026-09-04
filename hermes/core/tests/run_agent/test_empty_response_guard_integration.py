"""Focused integration tests for the empty-response guard in AIAgent."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def agent():
    from run_agent import AIAgent

    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        instance = AIAgent(
            api_key="test-key-1234567890",
            base_url="http://127.0.0.1:1234/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
        instance.client = MagicMock()
        instance._cached_system_prompt = "You are helpful."
        instance._use_prompt_caching = False
        instance.compression_enabled = False
        instance.save_trajectories = False
        return instance


def _empty_response(*, usage=None):
    message = SimpleNamespace(content=None, tool_calls=None)
    choice = SimpleNamespace(message=message, finish_reason="stop")
    return SimpleNamespace(
        choices=[choice], model="test/model", usage=usage
    )


def _zero_output_usage():
    return SimpleNamespace(
        prompt_tokens=25_900,
        completion_tokens=0,
        total_tokens=25_900,
    )


def _run(instance, message="answer me"):
    with (
        patch.object(instance, "_persist_session"),
        patch.object(instance, "_save_trajectory"),
        patch.object(instance, "_cleanup_task_resources"),
    ):
        return instance.run_conversation(message)


class TestEmptyResponseGuardIntegration:
    def test_missing_usage_preserves_three_retry_behavior(self, agent):
        empty = _empty_response()
        agent.client.chat.completions.create.side_effect = [empty] * 4

        result = _run(agent)

        assert result["final_response"] == "(empty)"
        assert result["api_calls"] == 4
        assert agent.client.chat.completions.create.call_count == 4
        assert result["failed"] is True
        assert result["failure_reason"] == "empty_response"
        assert result["failure_retryable"] is False
        assert result["error_surface"]["code"] == "empty_response"

    def test_two_identical_zero_output_responses_skip_remaining_retries(self, agent):
        empty = _empty_response(usage=_zero_output_usage())
        agent.client.chat.completions.create.side_effect = [empty, empty]

        with patch("agent.empty_response_guard._estimate_attempt_cost", return_value=None):
            result = _run(agent)

        assert result["final_response"] == "(empty)"
        assert result["api_calls"] == 2
        assert agent.client.chat.completions.create.call_count == 2

    def test_known_high_cost_attempt_reduces_retry_budget_to_one(self, agent):
        # Keep output usage non-zero so this assertion exercises the cost
        # budget independently of deterministic-empty short-circuiting.
        usage = SimpleNamespace(
            prompt_tokens=25_900,
            completion_tokens=1,
            total_tokens=25_901,
        )
        empty = _empty_response(usage=usage)
        agent.client.chat.completions.create.side_effect = [empty, empty]

        with patch(
            "agent.empty_response_guard._estimate_attempt_cost",
            return_value=Decimal("0.80"),
        ):
            result = _run(agent)

        # A budget of one means the initial call plus one retry.
        assert result["final_response"] == "(empty)"
        assert result["api_calls"] == 2
        assert agent.client.chat.completions.create.call_count == 2

    def test_disabled_guard_restores_three_retry_behavior(self, agent):
        empty = _empty_response(usage=_zero_output_usage())
        agent.client.chat.completions.create.side_effect = [empty] * 4
        agent._empty_guard_enabled = False

        with patch("agent.empty_response_guard._estimate_attempt_cost", return_value=None):
            result = _run(agent)

        assert result["final_response"] == "(empty)"
        assert result["api_calls"] == 4
        assert agent.client.chat.completions.create.call_count == 4
