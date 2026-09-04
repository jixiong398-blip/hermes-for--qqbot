"""Offline tests for the bounded provider-projection compatibility port."""

from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

from agent.provider_projection import (
    MAX_PROJECTED_ROW_BYTES,
    MAX_PROJECTED_ROWS,
    splice_provider_projection,
)


def test_import_does_not_load_agent_loop_or_open_runtime_state():
    run_agent_was_loaded = "run_agent" in sys.modules
    sys.modules.pop("agent.provider_projection", None)
    module = importlib.import_module("agent.provider_projection")

    assert module is not None
    # The full run_agent suite may have imported the loop earlier in this
    # process.  The port must not introduce a new import, regardless of test
    # collection order.
    assert ("run_agent" in sys.modules) == run_agent_was_loaded


def test_projection_rows_are_copied_stamped_and_iterations_tick():
    agent = SimpleNamespace(provider="acp-agent", _iters_since_skill=2)
    source_row = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "call-1", "function": {"name": "edit"}}],
    }
    messages = [{"role": "user", "content": "edit"}]

    count = splice_provider_projection(
        agent,
        SimpleNamespace(
            hermes_projected_messages=[source_row, {"role": "tool", "content": "done"}],
            hermes_provider_tool_iterations=3,
        ),
        messages,
    )

    assert count == 2
    assert messages[1]["timestamp"] > 0
    assert messages[2]["timestamp"] > 0
    assert agent._iters_since_skill == 5
    assert "timestamp" not in source_row


def test_projection_rejects_unsafe_rows_and_bounds_iterations():
    agent = SimpleNamespace(_iters_since_skill=0)
    messages = []
    projected = [
        {"role": "user", "content": "must be ignored"},
        {"role": "tool", "content": "\x00bad"},
        {"role": "tool", "content": "ok"},
    ]

    assert splice_provider_projection(
        agent,
        SimpleNamespace(
            hermes_projected_messages=projected,
            hermes_provider_tool_iterations="not-a-number",
        ),
        messages,
    ) == 1
    assert messages[0]["role"] == "tool"
    assert agent._iters_since_skill == 0

    splice_provider_projection(
        agent,
        SimpleNamespace(hermes_provider_tool_iterations=MAX_PROJECTED_ROWS * 100),
        messages,
    )
    assert agent._iters_since_skill == 1000


def test_projection_caps_rows_and_is_noop_for_ordinary_response():
    agent = SimpleNamespace(_iters_since_skill=1)
    messages = []
    rows = [{"role": "tool", "content": str(i)} for i in range(MAX_PROJECTED_ROWS + 10)]

    assert splice_provider_projection(agent, SimpleNamespace(hermes_projected_messages=rows), messages) == MAX_PROJECTED_ROWS
    assert len(messages) == MAX_PROJECTED_ROWS
    before = list(messages)
    assert splice_provider_projection(agent, SimpleNamespace(choices=[]), messages) == 0
    assert messages == before
    assert agent._iters_since_skill == 1


def test_projection_rejects_oversized_nested_tool_payload():
    agent = SimpleNamespace(_iters_since_skill=0)
    messages = []
    oversized = {
        "role": "tool",
        "content": "ok",
        "metadata": "x" * MAX_PROJECTED_ROW_BYTES,
    }

    assert splice_provider_projection(
        agent,
        SimpleNamespace(hermes_projected_messages=[oversized]),
        messages,
    ) == 0
    assert messages == []
