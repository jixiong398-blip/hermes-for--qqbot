"""Contract tests for the staged upstream message-sanitization port."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import agent.message_sanitization as port


def test_import_does_not_load_run_agent():
    core_dir = Path(__file__).parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import agent.message_sanitization; "
            "assert 'run_agent' not in sys.modules; print('ok')",
        ],
        cwd=core_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "ok"


def test_surrogate_and_ascii_ports_match_local_helpers():
    from run_agent import (
        _sanitize_messages_non_ascii,
        _sanitize_messages_surrogates,
        _sanitize_structure_non_ascii,
        _sanitize_structure_surrogates,
        _sanitize_surrogates,
        _strip_non_ascii,
    )

    assert port._sanitize_surrogates("a\ud800b") == _sanitize_surrogates("a\ud800b")
    messages_a = [{"role": "user", "content": "a\ud800b", "name": "\u2603"}]
    messages_b = [{"role": "user", "content": "a\ud800b", "name": "\u2603"}]
    assert port._sanitize_messages_surrogates(messages_a) == _sanitize_messages_surrogates(messages_b)
    assert messages_a == messages_b

    structure_a = {"nested": ["\udfff", "\u2603"]}
    structure_b = {"nested": ["\udfff", "\u2603"]}
    assert port._sanitize_structure_surrogates(structure_a) == _sanitize_structure_surrogates(structure_b)
    assert structure_a == structure_b

    ascii_a = {"nested": ["\u2603"]}
    ascii_b = {"nested": ["\u2603"]}
    assert port._sanitize_structure_non_ascii(ascii_a) == _sanitize_structure_non_ascii(ascii_b)
    assert ascii_a == ascii_b
    assert port._strip_non_ascii("A\u2603B") == _strip_non_ascii("A\u2603B")


def test_tool_argument_repair_and_image_strip_match_local_helpers():
    from run_agent import _repair_tool_call_arguments, _strip_images_from_messages

    raw = '{"value": 1,}'
    assert port._repair_tool_call_arguments(raw, "tool") == _repair_tool_call_arguments(raw, "tool")

    messages_a = [
        {"role": "assistant", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": [{"type": "image_url"}]},
    ]
    messages_b = [
        {"role": "assistant", "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": [{"type": "image_url"}]},
    ]
    assert port._strip_images_from_messages(messages_a) == _strip_images_from_messages(messages_b)
    assert messages_a == messages_b


def test_close_interrupted_tool_sequence_is_bounded_and_idempotent():
    messages = [{"role": "tool", "content": "done"}]
    assert port.close_interrupted_tool_sequence(messages) is True
    assert messages[-1] == {"role": "assistant", "content": "Operation interrupted."}
    assert port.close_interrupted_tool_sequence(messages) is False
    assert port.close_interrupted_tool_sequence([]) is False
    assert port.close_interrupted_tool_sequence([{"role": "user", "content": "x"}]) is False
