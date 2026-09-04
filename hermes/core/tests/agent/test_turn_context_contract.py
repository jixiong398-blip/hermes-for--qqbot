"""Offline contract tests for the staged upstream TurnContext port."""

from __future__ import annotations

import subprocess
import sys
from dataclasses import fields
from pathlib import Path

from agent.turn_context_contract import (
    TurnContext,
    _compression_made_progress,
    _compression_warrants_another_preflight_pass,
    _review_fork_first_request_pending,
    _should_idle_compact,
    _should_run_preflight_estimate,
    append_notes_to_multimodal_content,
    clone_message_for_api,
    compose_user_api_content,
    consume_gateway_turn_context_notes,
    drop_stale_api_content,
    extract_api_content_sidecar,
    reanchor_current_turn_user_idx,
    substitute_api_content,
)


def test_import_does_not_load_runtime_or_storage_modules():
    core_dir = Path(__file__).parents[2]
    code = (
        "import sys\n"
        "import agent.turn_context_contract\n"
        "assert 'run_agent' not in sys.modules\n"
        "assert 'hermes_state' not in sys.modules\n"
        "assert 'gateway.run' not in sys.modules\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=core_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "ok"


def test_turn_context_fields_match_the_frozen_contract():
    assert [item.name for item in fields(TurnContext)] == [
        "user_message",
        "original_user_message",
        "messages",
        "conversation_history",
        "active_system_prompt",
        "effective_task_id",
        "turn_id",
        "current_turn_user_idx",
        "should_review_memory",
        "plugin_user_context",
        "ext_prefetch_cache",
        "preflight_compression_blocked",
    ]
    context = TurnContext(
        user_message="hello",
        original_user_message="hello",
        messages=[{"role": "user", "content": "hello"}],
        conversation_history=None,
        active_system_prompt="system",
        effective_task_id="task",
        turn_id="turn",
        current_turn_user_idx=0,
    )
    assert context.should_review_memory is False
    assert context.preflight_compression_blocked is False


def test_compose_user_api_content_keeps_clean_content_separate(monkeypatch):
    monkeypatch.setattr(
        "agent.memory_manager.build_memory_context_block",
        lambda value: f"<memory>{value}</memory>",
    )
    clean = "user question"
    composed = compose_user_api_content(clean, "remembered", "plugin hint")
    assert composed == "user question\n\n<memory>remembered</memory>\n\nplugin hint"
    assert clean == "user question"
    assert compose_user_api_content([{"type": "text", "text": "image"}], "x", "y") is None
    assert compose_user_api_content(clean, "", "") is None


def test_sidecar_helpers_only_mutate_the_api_copy():
    message = {"role": "user", "content": "clean", "api_content": "wire"}
    assert extract_api_content_sidecar(message) == "wire"
    api_copy = dict(message)
    assert substitute_api_content(api_copy) == "wire"
    assert api_copy == {"role": "user", "content": "wire"}
    assert message["content"] == "clean"
    assert "api_content" in message
    drop_stale_api_content(message)
    assert "api_content" not in message

    tool_message = {"role": "tool", "content": "tool", "api_content": "ignored"}
    assert substitute_api_content(tool_message) == "ignored"
    assert tool_message == {"role": "tool", "content": "tool"}


def test_clone_message_for_api_detaches_nested_state_and_projects_sidecar():
    canonical = {
        "role": "user",
        "content": "clean transcript",
        "timestamp": 123.5,
        "api_content": "provider-only context",
        "display_kind": "internal_notification",
        "display_metadata": {"visible": False},
        "metadata": {"parts": [{"text": "canonical"}]},
        "tool_calls": [
            {"id": "call-1", "function": {"name": "tool", "arguments": "{}"}}
        ],
    }

    projected = clone_message_for_api(canonical)

    assert projected["content"] == "provider-only context"
    assert "api_content" not in projected
    assert "timestamp" not in projected
    assert "display_kind" not in projected
    assert "display_metadata" not in projected
    projected["metadata"]["parts"][0]["text"] = "changed"
    projected["tool_calls"][0]["function"]["arguments"] = '{"x":1}'

    assert canonical["content"] == "clean transcript"
    assert canonical["timestamp"] == 123.5
    assert canonical["api_content"] == "provider-only context"
    assert canonical["display_kind"] == "internal_notification"
    assert canonical["display_metadata"] == {"visible": False}
    assert canonical["metadata"]["parts"][0]["text"] == "canonical"
    assert canonical["tool_calls"][0]["function"]["arguments"] == "{}"


def test_clone_message_for_api_preserves_an_empty_user_turn_without_sidecar():
    canonical = {"role": "user", "content": ""}

    projected = clone_message_for_api(canonical)

    assert projected == canonical
    assert projected is not canonical


def test_gateway_notes_are_one_shot_and_multimodal_append_is_bounded_to_a_list():
    class Agent:
        _gateway_turn_context_notes = "once"

    agent = Agent()
    assert consume_gateway_turn_context_notes(agent) == "once"
    assert consume_gateway_turn_context_notes(agent) == ""

    content = [{"type": "image_url", "image_url": {"url": "local"}}]
    assert append_notes_to_multimodal_content(content, "note") is True
    assert content[-1] == {"type": "text", "text": "note"}
    assert append_notes_to_multimodal_content("not a list", "note") is False
    assert append_notes_to_multimodal_content([], "") is False


def test_reanchor_prefers_exact_user_content_and_skips_synthetic_fallbacks():
    messages = [
        {"role": "user", "content": "old"},
        {"role": "user", "content": "synthetic", "_synthetic": True},
        {"role": "user", "content": "current"},
    ]
    assert reanchor_current_turn_user_idx(messages, "current") == 2
    rewritten = [
        {"role": "user", "content": "old"},
        {"role": "user", "content": "handoff", "synthetic": True},
    ]
    assert reanchor_current_turn_user_idx(rewritten, "rewritten") == 0
    assert reanchor_current_turn_user_idx([], "missing") == -1
    display_only = [
        {"role": "user", "content": "internal", "display_kind": "internal_notification"},
        {"role": "user", "content": "   "},
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]},
    ]
    assert reanchor_current_turn_user_idx(display_only, "rewritten") == 2


def test_compression_predicates_preserve_upstream_thresholds():
    assert _compression_made_progress(10, 9, 100, 100) is True
    assert _compression_made_progress(10, 10, 100, 94) is True
    assert _compression_made_progress(10, 10, 100, 95) is False
    assert _compression_warrants_another_preflight_pass(100, 94, 90) is True
    assert _compression_warrants_another_preflight_pass(100, 96, 90) is False
    assert _should_run_preflight_estimate([{"role": "user", "content": "x"}] * 6, 2, 2, 999999) is True
    assert _should_run_preflight_estimate([{"role": "user", "content": "x"}], 2, 2, 999999) is False
    assert _should_idle_compact(
        enabled=True,
        idle_after_seconds=60,
        idle_gap_seconds=60,
        tokens=101,
        floor_tokens=100,
        cooldown_active=False,
    ) is True
    assert _should_idle_compact(
        enabled=True,
        idle_after_seconds=60,
        idle_gap_seconds=60,
        tokens=101,
        floor_tokens=100,
        cooldown_active=True,
    ) is False


def test_review_fork_gate_is_inert_without_explicit_agent_flags():
    class Agent:
        pass

    assert _review_fork_first_request_pending(Agent()) is False
    agent = Agent()
    agent._review_defer_compaction_before_first_response = True
    assert _review_fork_first_request_pending(agent) is True
    agent._turn_received_provider_response = True
    assert _review_fork_first_request_pending(agent) is False
