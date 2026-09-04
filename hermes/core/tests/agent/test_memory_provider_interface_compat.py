"""Compatibility tests for the incremental MemoryProvider contract."""

from __future__ import annotations

import inspect

import pytest

from agent.memory_provider import (
    INDICATOR_GLYPH,
    PRE_COMPRESS_CHECKPOINT_API_VERSION,
    RecallStatus,
    MemoryProvider,
    is_trivial_prompt,
)


class _LegacyProvider(MemoryProvider):
    """Provider written against the local pre-compatibility interface."""

    @property
    def name(self) -> str:
        return "legacy"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        self.session_id = session_id

    def sync_turn(self, user_content, assistant_content, *, session_id=""):
        self.last_sync = (user_content, assistant_content, session_id)

    def get_tool_schemas(self):
        return []


class _EnrichedProvider(MemoryProvider):
    """Provider opting into the new optional arguments."""

    @property
    def name(self) -> str:
        return "enriched"

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs) -> None:
        self.session_id = session_id

    def sync_turn(
        self,
        user_content,
        assistant_content,
        *,
        session_id="",
        messages=None,
    ):
        self.sync_args = {
            "user": user_content,
            "assistant": assistant_content,
            "session_id": session_id,
            "messages": messages,
        }

    def get_tool_schemas(self):
        return []

    def on_session_switch(
        self,
        new_session_id,
        *,
        parent_session_id="",
        reset=False,
        rewound=False,
        **kwargs,
    ):
        self.switch_args = {
            "new_session_id": new_session_id,
            "parent_session_id": parent_session_id,
            "reset": reset,
            "rewound": rewound,
            "extra": kwargs,
        }


def test_checkpoint_version_and_legacy_default_are_distinct():
    assert PRE_COMPRESS_CHECKPOINT_API_VERSION == 2
    assert _LegacyProvider.pre_compress_checkpoint_api_version == 1
    assert _EnrichedProvider.pre_compress_checkpoint_api_version == 1


def test_legacy_provider_keeps_all_default_compatibility_hooks():
    provider = _LegacyProvider()

    assert provider.unavailable_reason() == ""
    assert provider.recall_status() is None
    assert provider.backup_paths() == []
    assert provider.backup_paths() is not provider.backup_paths()

    # New keyword arguments are optional at the base-contract level; the
    # legacy implementation itself remains callable with its old signature.
    provider.sync_turn("user", "assistant", session_id="session")
    provider.on_session_switch(
        "session",
        parent_session_id="parent",
        reset=False,
        rewound=True,
    )
    assert provider.last_sync == ("user", "assistant", "session")


def test_enriched_provider_receives_messages_and_rewound():
    provider = _EnrichedProvider()
    messages = [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    ]

    provider.sync_turn(
        "question",
        "answer",
        session_id="session",
        messages=messages,
    )
    provider.on_session_switch(
        "session",
        parent_session_id="parent",
        reset=False,
        rewound=True,
        reason="undo",
    )

    assert provider.sync_args == {
        "user": "question",
        "assistant": "answer",
        "session_id": "session",
        "messages": messages,
    }
    assert provider.switch_args == {
        "new_session_id": "session",
        "parent_session_id": "parent",
        "reset": False,
        "rewound": True,
        "extra": {"reason": "undo"},
    }


def test_new_signature_parameters_are_optional():
    sync_parameters = inspect.signature(MemoryProvider.sync_turn).parameters
    switch_parameters = inspect.signature(MemoryProvider.on_session_switch).parameters

    assert sync_parameters["messages"].default is None
    assert switch_parameters["rewound"].default is False


def test_recall_status_is_frozen_observation_value():
    status = RecallStatus("Local memory", 3)
    assert status.provider_label == "Local memory"
    assert status.count == 3
    assert status.glyph == INDICATOR_GLYPH
    assert status == RecallStatus("Local memory", 3)

    with pytest.raises(AttributeError):
        status.count = 4


@pytest.mark.parametrize(
    "prompt",
    [
        "",
        "   ",
        "/help",
        "hi",
        "HI!",
        "thanks :)",
        "done???",
        "continue...",
    ],
)
def test_trivial_prompt_helper_matches_only_non_semantic_prompts(prompt):
    assert is_trivial_prompt(prompt)


@pytest.mark.parametrize(
    "prompt",
    [
        "k8s",
        "yolo",
        "note",
        "supper",
        "hello world",
        "continue the migration plan",
        "what is my name",
    ],
)
def test_trivial_prompt_helper_does_not_match_prefix_words(prompt):
    assert not is_trivial_prompt(prompt)


def test_trivial_prompt_helper_does_not_change_provider_or_platform_state():
    provider = _LegacyProvider()
    assert is_trivial_prompt("hello") is True
    assert provider.name == "legacy"
    assert provider.is_available() is True
