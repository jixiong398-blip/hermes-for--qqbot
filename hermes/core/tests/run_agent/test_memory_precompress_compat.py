"""Focused run-agent tests for memory hook capability detection."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from run_agent import AIAgent


class _StrictMemoryManager:
    def __init__(self, *, fail: bool = False):
        self.calls = []
        self.fail = fail

    def on_pre_compress(self, messages):
        self.calls.append(messages)
        if self.fail:
            raise TypeError("provider body failure")
        return "legacy checkpoint context"


class _CaptureCompressor:
    compression_count = 0
    _last_summary_error = None
    _last_aux_model_failure_model = None
    _last_aux_model_failure_error = None

    def __init__(self, *, fail: bool = False):
        self.memory_context = None
        self.fail = fail

    def compress(
        self,
        messages,
        current_tokens=None,
        focus_topic=None,
        memory_context="",
    ):
        if self.fail:
            raise TypeError("context engine body failure")
        self.memory_context = memory_context
        return list(messages)


def _make_agent(memory_manager, compressor):
    agent = AIAgent.__new__(AIAgent)
    agent._memory_manager = memory_manager
    agent.context_compressor = compressor
    agent.compression_checkpoint_required = False
    agent.session_id = "session-1"
    agent.model = "test-model"
    agent.platform = "cli"
    agent.api_mode = "chat_completions"
    agent.tools = []
    agent.log_prefix = ""
    agent.logs_dir = None
    agent.session_log_file = None
    agent._session_db = None
    agent._session_db_created = False
    agent._session_init_model_config = {}
    agent._cached_system_prompt = "system"
    agent._last_flushed_db_idx = 0
    agent._last_compression_summary_warning = None
    agent._last_aux_fallback_warning_key = None
    agent._todo_store = MagicMock()
    agent._todo_store.format_for_injection.return_value = ""
    agent._emit_warning = MagicMock()
    agent._vprint = lambda *args, **kwargs: None
    agent._invalidate_system_prompt = lambda: None
    agent._build_system_prompt = lambda _system_message: "system"
    agent.commit_memory_session = lambda _messages: None
    return agent


def test_legacy_pre_compress_hook_is_called_once_without_enriched_kwargs():
    manager = _StrictMemoryManager()
    compressor = _CaptureCompressor()
    agent = _make_agent(manager, compressor)
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "keep this"},
    ]

    compressed, _system = agent._compress_context(
        messages,
        "system",
        approx_tokens=100,
    )

    assert compressed == messages
    assert len(manager.calls) == 1
    assert compressor.memory_context == "legacy checkpoint context"


def test_provider_typeerror_is_not_retried_as_signature_fallback():
    manager = _StrictMemoryManager(fail=True)
    compressor = _CaptureCompressor()
    agent = _make_agent(manager, compressor)

    agent._compress_context(
        [{"role": "user", "content": "keep this"}],
        "system",
        approx_tokens=100,
    )

    assert len(manager.calls) == 1
    assert compressor.memory_context == ""


def test_legacy_context_engine_skips_unsupported_memory_kwargs():
    class _StrictContextEngine:
        compression_count = 0
        _last_summary_error = None
        _last_aux_model_failure_model = None
        _last_aux_model_failure_error = None

        def __init__(self):
            self.calls = []

        def compress(self, messages, current_tokens=None):
            self.calls.append(current_tokens)
            self.compression_count += 1
            return list(messages)

    engine = _StrictContextEngine()
    agent = _make_agent(None, engine)

    compressed, _system = agent._compress_context(
        [{"role": "user", "content": "keep this"}],
        "system",
        approx_tokens=100,
        focus_topic="topic",
    )

    assert compressed == [{"role": "user", "content": "keep this"}]
    assert engine.calls == [100]


def test_context_engine_typeerror_is_not_retried_as_signature_fallback():
    compressor = _CaptureCompressor(fail=True)
    agent = _make_agent(None, compressor)

    with pytest.raises(TypeError, match="context engine body failure"):
        agent._compress_context(
            [{"role": "user", "content": "keep this"}],
            "system",
            approx_tokens=100,
        )


def test_run_conversation_prefetch_is_scoped_to_current_session():
    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
            model="test-model",
            session_id="session-1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )

    agent._cached_system_prompt = "system"
    agent._use_prompt_caching = False
    agent.tool_delay = 0
    agent.compression_enabled = False
    agent.save_trajectories = False
    agent._memory_manager = MagicMock()
    agent._memory_manager.build_system_prompt.return_value = ""
    agent._memory_manager.prefetch_all.return_value = ""
    agent._memory_manager.describe_recall.return_value = ""
    agent.client = MagicMock()
    agent.client.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="answer", tool_calls=None),
                finish_reason="stop",
            )
        ],
        model="test-model",
        usage=None,
    )

    with (
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("remember this decision")

    assert result["final_response"] == "answer"
    agent._memory_manager.prefetch_all.assert_called_once_with(
        "remember this decision",
        session_id="session-1",
    )


def test_run_conversation_keeps_legacy_prefetch_manager_compatible():
    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
            model="test-model",
            session_id="session-legacy",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )

    agent._cached_system_prompt = "system"
    agent._use_prompt_caching = False
    agent.tool_delay = 0
    agent.compression_enabled = False
    agent.save_trajectories = False
    manager = MagicMock()
    manager.build_system_prompt.return_value = ""
    manager.describe_recall.return_value = ""
    seen_queries = []

    def legacy_prefetch(query):
        seen_queries.append(query)
        return ""

    manager.prefetch_all = legacy_prefetch
    agent._memory_manager = manager
    agent.client = MagicMock()
    agent.client.chat.completions.create.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="answer", tool_calls=None),
                finish_reason="stop",
            )
        ],
        model="test-model",
        usage=None,
    )

    with (
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("legacy manager query")

    assert result["final_response"] == "answer"
    assert seen_queries == ["legacy manager query"]
