"""Integration checks for the run_agent error-result boundary."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.error_classifier import ClassifiedError, FailoverReason
from run_agent import _attach_error_surface_to_run_result


class _Agent:
    provider = "openrouter"
    model = "provider/model"
    _last_classified_error = ClassifiedError(
        reason=FailoverReason.rate_limit,
        status_code=429,
        retryable=True,
    )


@pytest.fixture()
def live_agent():
    from run_agent import AIAgent

    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        instance = AIAgent(
            api_key="test-key-1234567890",
            base_url="http://127.0.0.1:1234/v1",
            provider="openrouter",
            model="provider/model",
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


def test_run_conversation_boundary_enriches_failed_result():
    @_attach_error_surface_to_run_result
    def fake_run(self):
        return {
            "completed": False,
            "failed": True,
            "error": "too many requests",
        }

    result = fake_run(_Agent())
    assert result["error_surface"] == {
        "layer": "provider",
        "code": "rate_limit",
        "retryable": True,
        "provider": "openrouter",
        "model": "provider/model",
        "message": "too many requests",
    }
    assert result["failure_reason"] == "rate_limit"
    assert result["failure_retryable"] is True
    assert result["status_code"] == 429


def test_run_conversation_boundary_preserves_success_shape():
    result = {"completed": True, "final_response": "ok"}

    @_attach_error_surface_to_run_result
    def fake_run(self):
        return result

    assert fake_run(_Agent()) is result


def test_empty_sentinel_keeps_user_facing_text_and_gets_failure_metadata():
    @_attach_error_surface_to_run_result
    def fake_run(self):
        return {
            "completed": True,
            "final_response": "(empty)",
            "messages": [],
        }

    result = fake_run(_Agent())
    assert result["final_response"] == "(empty)"
    assert result["failed"] is True
    assert result["failure_reason"] == "empty_response"
    assert result["failure_retryable"] is False
    assert result["error_surface"]["code"] == "empty_response"


def test_terminal_error_exit_gets_classified_metadata():
    @_attach_error_surface_to_run_result
    def fake_run(self):
        return {
            "completed": True,
            "final_response": "I apologize, but I encountered repeated errors",
            "turn_exit_reason": "error_near_max_iterations(provider failed)",
        }

    result = fake_run(_Agent())
    assert result["failed"] is True
    assert result["failure_reason"] == "rate_limit"
    assert result["failure_retryable"] is True
    assert result["status_code"] == 429


def test_real_run_conversation_keeps_classified_status_metadata(live_agent):
    class HTTP429Error(Exception):
        status_code = 429

    live_agent.client.chat.completions.create.side_effect = HTTP429Error(
        "too many requests"
    )
    with (
        patch.object(live_agent, "_persist_session"),
        patch.object(live_agent, "_save_trajectory"),
        patch.object(live_agent, "_cleanup_task_resources"),
        patch("run_agent.jittered_backoff", return_value=0),
    ):
        result = live_agent.run_conversation("hello")

    assert result["failed"] is True
    assert result["failure_reason"] == "rate_limit"
    assert result["failure_retryable"] is True
    assert result["status_code"] == 429
    assert result["error_surface"]["code"] == "rate_limit"
