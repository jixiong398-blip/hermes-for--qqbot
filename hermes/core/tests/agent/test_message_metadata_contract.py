"""Offline tests for the staged upstream message metadata port."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from agent.message_metadata_contract import (
    PERSISTENCE_ONLY_MESSAGE_FIELDS,
    append_message,
    stamp_message_timestamp,
)


def test_import_has_no_runtime_or_storage_side_effects():
    core_dir = Path(__file__).parents[2]
    code = (
        "import sys\n"
        "import agent.message_metadata_contract\n"
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


def test_timestamp_stamp_preserves_existing_and_accepts_source_time():
    message = {"role": "user", "content": "hello"}
    returned = stamp_message_timestamp(message, timestamp=123.5)
    assert returned is message
    assert message["timestamp"] == 123.5

    stamp_message_timestamp(message, timestamp=999.0)
    assert message["timestamp"] == 123.5

    existing = {"role": "assistant", "content": "reply", "timestamp": 7.0}
    assert stamp_message_timestamp(existing, timestamp=9.0) is existing
    assert existing["timestamp"] == 7.0


def test_append_message_stamps_before_appending_and_returns_same_mapping():
    messages = []
    message = {"role": "user", "content": "hello"}
    assert append_message(messages, message, timestamp=42.0) is message
    assert messages == [{"role": "user", "content": "hello", "timestamp": 42.0}]

    second = {"role": "assistant", "content": "reply", "timestamp": 43.0}
    append_message(messages, second, timestamp=100.0)
    assert messages[-1]["timestamp"] == 43.0


def test_persistence_only_field_contract_is_explicit_and_immutable():
    assert PERSISTENCE_ONLY_MESSAGE_FIELDS == frozenset({"timestamp"})
    try:
        PERSISTENCE_ONLY_MESSAGE_FIELDS.add("content")
    except AttributeError:
        pass
    else:  # pragma: no cover - frozenset must be immutable
        raise AssertionError("persistence field set must be immutable")
