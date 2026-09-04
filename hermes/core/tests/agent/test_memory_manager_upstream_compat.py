"""Regression tests for the upstream-compatible MemoryManager caller port.

These tests deliberately use both historical provider signatures and the
newer enriched hooks.  The manager must adapt at the call boundary without
retrying a provider body error as a signature error.
"""

from typing import Any, Dict, List, Optional

import pytest

from agent.memory_manager import MemoryManager, direct_messages_for_pre_compress_memory
from agent.memory_provider import (
    PRE_COMPRESS_CHECKPOINT_API_VERSION,
    MemoryProvider,
    RecallStatus,
)


class _BaseProvider(MemoryProvider):
    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str = "", **kwargs: Any) -> None:
        pass

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return []


class _LegacySyncProvider(_BaseProvider):
    def __init__(self, name: str = "legacy"):
        super().__init__(name)
        self.calls: List[tuple[str, str, str]] = []

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        self.calls.append((user_content, assistant_content, session_id))


class _EnrichedSyncProvider(_BaseProvider):
    def __init__(self, name: str = "enriched"):
        super().__init__(name)
        self.calls: List[Dict[str, Any]] = []

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        self.calls.append(
            {
                "user": user_content,
                "assistant": assistant_content,
                "session_id": session_id,
                "messages": messages,
            }
        )


def test_sync_all_adapts_legacy_and_enriched_signatures_without_typeerror_retry():
    manager = MemoryManager()
    legacy = _LegacySyncProvider("builtin")
    enriched = _EnrichedSyncProvider()
    manager.add_provider(legacy)
    manager.add_provider(enriched)

    messages = [
        {"role": "user", "content": "inspect the project"},
        {"role": "assistant", "tool_calls": [{"id": "call-1"}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "done"},
    ]
    manager.sync_all(
        "inspect the project",
        "done",
        session_id="session-1",
        messages=messages,
    )

    assert legacy.calls == [("inspect the project", "done", "session-1")]
    assert enriched.calls == [
        {
            "user": "inspect the project",
            "assistant": "done",
            "session_id": "session-1",
            "messages": messages,
        }
    ]


def test_sync_body_typeerror_is_not_retried_as_legacy_call():
    manager = MemoryManager()
    provider = _EnrichedSyncProvider("builtin")
    calls = {"count": 0}

    def failing_sync(*args: Any, **kwargs: Any) -> None:
        calls["count"] += 1
        raise TypeError("provider body failure")

    provider.sync_turn = failing_sync  # type: ignore[method-assign]
    manager.add_provider(provider)

    manager.sync_all(
        "user",
        "assistant",
        messages=[{"role": "user", "content": "user"}],
    )

    assert calls["count"] == 1


class _RecallProvider(_BaseProvider):
    def __init__(self, name: str, status: Optional[RecallStatus]):
        super().__init__(name)
        self.status = status

    def recall_status(self) -> Optional[RecallStatus]:
        return self.status


def test_describe_recall_is_model_independent_and_bounded_to_provider_status():
    manager = MemoryManager()
    manager.add_provider(_RecallProvider("builtin", None))
    manager.add_provider(_RecallProvider("hindsight", RecallStatus("Hindsight", 2)))

    assert manager.describe_recall() == "🧠 Hindsight — recalled 2 memories"


class _LegacySwitchProvider(_BaseProvider):
    def __init__(self, name: str = "legacy-switch"):
        super().__init__(name)
        self.calls = []

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
    ) -> None:
        self.calls.append((new_session_id, parent_session_id, reset))


class _RewindSwitchProvider(_LegacySwitchProvider):
    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
    ) -> None:
        self.calls.append((new_session_id, parent_session_id, reset, rewound))


def test_on_session_switch_only_passes_rewound_to_capable_provider():
    manager = MemoryManager()
    legacy = _LegacySwitchProvider("builtin")
    capable = _RewindSwitchProvider("rewind")
    manager.add_provider(legacy)
    manager.add_provider(capable)

    manager.on_session_switch(
        "session-2",
        parent_session_id="session-1",
        rewound=True,
    )

    assert legacy.calls == [("session-2", "session-1", False)]
    assert capable.calls == [("session-2", "session-1", False, True)]


class _CheckpointProvider(_BaseProvider):
    pre_compress_checkpoint_api_version = PRE_COMPRESS_CHECKPOINT_API_VERSION

    def __init__(self, name: str = "checkpoint"):
        super().__init__(name)
        self.received = []

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        self.received.append(messages)
        return "checkpoint context"


def test_v2_checkpoint_receives_evidence_and_can_be_required():
    manager = MemoryManager()
    provider = _CheckpointProvider("builtin")
    manager.add_provider(provider)
    raw = [{"role": "tool", "content": "implementation detail"}]
    evidence = [{"role": "user", "content": "durable decision"}]

    result = manager.on_pre_compress(
        raw,
        evidence_messages=evidence,
        require_checkpoint=True,
    )

    assert result == "checkpoint context"
    assert provider.received == [evidence]


def test_require_checkpoint_rejects_legacy_only_manager():
    manager = MemoryManager()
    manager.add_provider(_LegacySyncProvider("builtin"))

    with pytest.raises(RuntimeError, match="pre-compress checkpoint"):
        manager.on_pre_compress(
            [{"role": "user", "content": "evidence"}],
            require_checkpoint=True,
        )


def test_direct_pre_compress_evidence_excludes_derivative_and_tool_scaffolding():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "keep this decision"},
        {
            "role": "assistant",
            "content": "I checked the result.",
            "tool_calls": [{"id": "call-1"}],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "raw output"},
        {"role": "assistant", "content": "derived summary", "_compressed_summary": True},
        {"role": "assistant", "content": "[CONTEXT COMPACTION — REFERENCE ONLY] old"},
        {"role": "assistant", "tool_calls": [{"id": "call-2"}], "content": ""},
    ]

    evidence = direct_messages_for_pre_compress_memory(messages)

    assert evidence == [
        {"role": "user", "content": "keep this decision"},
        {"role": "assistant", "content": "I checked the result."},
    ]
    assert messages[2]["tool_calls"]
