"""Import-compatible message sanitization port for the local Agent Runtime.

The upstream Hermes module also owns provider-specific call-id and
``reasoning_content`` policies.  The local fork has different owners for those
paths, so this first port exposes only sanitizers that already exist and are
tested in ``run_agent.py``.  Every implementation is resolved lazily: importing
this module cannot initialize the large agent loop or provider clients.
"""

from __future__ import annotations

try:
    import hermes_bootstrap  # noqa: F401
except ModuleNotFoundError:
    hermes_bootstrap = None  # type: ignore[assignment]

from importlib import import_module
from typing import Any, Callable


def _local_helper(name: str) -> Callable[..., Any]:
    """Resolve one already-tested local helper only when it is called."""

    module = import_module("run_agent")
    helper = getattr(module, name, None)
    if not callable(helper):
        raise RuntimeError(f"local message sanitizer is unavailable: {name}")
    return helper


def _sanitize_surrogates(text: str) -> str:
    """Replace lone UTF-16 surrogate code points with U+FFFD."""

    return _local_helper("_sanitize_surrogates")(text)


def _sanitize_structure_surrogates(payload: Any) -> bool:
    """Sanitize surrogate characters in nested dictionaries/lists in place."""

    return bool(_local_helper("_sanitize_structure_surrogates")(payload))


def _sanitize_messages_surrogates(messages: list) -> bool:
    """Sanitize surrogate characters in an OpenAI-format message list in place."""

    return bool(_local_helper("_sanitize_messages_surrogates")(messages))


def _escape_invalid_chars_in_json_strings(raw: str) -> str:
    """Escape literal control characters inside JSON strings."""

    return _local_helper("_escape_invalid_chars_in_json_strings")(raw)


def _repair_tool_call_arguments(raw_args: str, tool_name: str = "?") -> str:
    """Reuse the local bounded malformed-tool-argument repair pipeline."""

    return _local_helper("_repair_tool_call_arguments")(raw_args, tool_name)


def _strip_non_ascii(text: str) -> str:
    """Apply the local ASCII fallback used by the agent retry path."""

    return _local_helper("_strip_non_ascii")(text)


def _sanitize_messages_non_ascii(messages: list) -> bool:
    """Strip unsupported non-ASCII data from messages in place."""

    return bool(_local_helper("_sanitize_messages_non_ascii")(messages))


def _sanitize_tools_non_ascii(tools: list) -> bool:
    """Strip unsupported non-ASCII data from tool schemas in place."""

    return bool(_local_helper("_sanitize_tools_non_ascii")(tools))


def _sanitize_structure_non_ascii(payload: Any) -> bool:
    """Strip unsupported non-ASCII data from a nested structure in place."""

    return bool(_local_helper("_sanitize_structure_non_ascii")(payload))


def _strip_images_from_messages(messages: list) -> bool:
    """Reuse local image-removal logic while preserving tool-call pairing."""

    return bool(_local_helper("_strip_images_from_messages")(messages))


def close_interrupted_tool_sequence(messages: list, final_response: Any = None) -> bool:
    """Close a transcript whose tail is a tool result.

    The local fork does not have the upstream ``message_metadata`` module, so
    this small adapter appends the same semantic assistant marker without
    importing the full agent loop.  Persistence/timestamp stamping remains the
    responsibility of the existing local SessionDB path.
    """

    if not isinstance(messages, list) or not messages:
        return False
    last = messages[-1]
    if not isinstance(last, dict) or last.get("role") != "tool":
        return False
    text = final_response if isinstance(final_response, str) else ""
    messages.append(
        {
            "role": "assistant",
            "content": text.strip() or "Operation interrupted.",
        }
    )
    return True


__all__ = [
    "close_interrupted_tool_sequence",
    "_sanitize_surrogates",
    "_sanitize_structure_surrogates",
    "_sanitize_messages_surrogates",
    "_escape_invalid_chars_in_json_strings",
    "_repair_tool_call_arguments",
    "_strip_non_ascii",
    "_sanitize_messages_non_ascii",
    "_sanitize_tools_non_ascii",
    "_sanitize_structure_non_ascii",
    "_strip_images_from_messages",
]
