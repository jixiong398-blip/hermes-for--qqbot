"""Pure compatibility contracts extracted from upstream ``turn_context``.

This module is deliberately smaller than upstream ``agent.turn_context``.  It
contains only data-shaping helpers and predicates that can be tested without a
provider, SessionDB, MCP server, filesystem side effect, or agent loop.  The
local ``run_agent`` prologue remains authoritative and is not imported here.
"""

from __future__ import annotations

try:
    import hermes_bootstrap  # noqa: F401
except ModuleNotFoundError:
    hermes_bootstrap = None  # type: ignore[assignment]

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional


def _clone_message_containers(value: Any) -> Any:
    """Clone JSON-shaped containers while sharing immutable leaf values."""

    if isinstance(value, dict):
        return {
            key: _clone_message_containers(item)
            if isinstance(item, (dict, list))
            else item
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _clone_message_containers(item)
            if isinstance(item, (dict, list))
            else item
            for item in value
        ]
    return value


def clone_message_for_api(message: Any) -> Any:
    """Build the provider-facing message copy without mutating the transcript.

    The local transcript remains the canonical owner of message content and
    persistence-only metadata. Provider preparation is allowed to rewrite
    nested content/tool-call containers, so a top-level ``dict.copy()`` is not
    sufficient. ``api_content`` is a sidecar for the API copy: it replaces
    user/assistant content on the clone and is never forwarded as a provider
    field. The helper intentionally does not stamp or persist sidecars.
    """

    cloned = _clone_message_containers(message)
    if isinstance(cloned, dict):
        substitute_api_content(cloned)
        # Persistence-only metadata belongs to the canonical transcript, never
        # to provider payloads. The event timestamp remains available to the
        # durable writer on the original mapping.
        cloned.pop("timestamp", None)
        # Display metadata is also a durable/UI concern. It must not be sent
        # to strict provider APIs as an unknown message field.
        cloned.pop("display_kind", None)
        cloned.pop("display_metadata", None)
    return cloned


def compose_user_api_content(
    content: Any,
    ext_prefetch_cache: str,
    plugin_user_context: str,
) -> Optional[str]:
    """Compose API-only user content while leaving the stored message clean."""

    if not isinstance(content, str):
        return None
    injections: list[str] = []
    if ext_prefetch_cache:
        # Lazy import keeps this contract module free of memory initialization
        # and preserves the local module import graph.
        from agent.memory_manager import build_memory_context_block

        fenced = build_memory_context_block(ext_prefetch_cache)
        if fenced:
            injections.append(fenced)
    if plugin_user_context:
        injections.append(plugin_user_context)
    if not injections:
        return None
    return content + "\n\n" + "\n\n".join(injections)


def substitute_api_content(api_msg: Dict[str, Any]) -> Optional[str]:
    """Replace a bounded ``api_content`` sidecar in an API-copy message."""

    if not isinstance(api_msg, dict):
        return None
    sidecar = api_msg.pop("api_content", None)
    if (
        isinstance(sidecar, str)
        and sidecar
        and api_msg.get("role") in ("user", "assistant")
    ):
        api_msg["content"] = sidecar
    return sidecar if isinstance(sidecar, str) else None


def drop_stale_api_content(msg: Dict[str, Any]) -> None:
    """Drop a sidecar after the canonical message content is rewritten."""

    if isinstance(msg, dict):
        msg.pop("api_content", None)


def extract_api_content_sidecar(msg: Mapping[str, Any]) -> Optional[str]:
    """Read a string sidecar for an explicit persistence boundary."""

    if not isinstance(msg, Mapping):
        return None
    value = msg.get("api_content")
    return value if isinstance(value, str) else None


def consume_gateway_turn_context_notes(agent: Any) -> str:
    """Consume one-shot gateway notes staged on an agent instance."""

    notes = getattr(agent, "_gateway_turn_context_notes", "") or ""
    if hasattr(agent, "_gateway_turn_context_notes"):
        try:
            agent._gateway_turn_context_notes = ""
        except Exception:
            pass
    return notes if isinstance(notes, str) else ""


def append_notes_to_multimodal_content(content: Any, notes: str) -> bool:
    """Append gateway notes as one text part to a mutable multimodal list."""

    if not notes or not isinstance(content, list):
        return False
    try:
        content.append({"type": "text", "text": notes})
        return True
    except Exception:
        return False


_SYNTHETIC_USER_MARKERS = (
    "_synthetic",
    "synthetic",
    "compressed_summary",
    "_compressed_summary",
    "micro_compact",
    "_micro_compact",
    "_compression_summary",
)


def _is_actionable_user_row(message: Any) -> bool:
    """Return whether a user-role row can be used as a real-turn anchor."""

    if not isinstance(message, dict) or message.get("role") != "user":
        return False
    if message.get("display_kind"):
        return False
    if any(message.get(marker) for marker in _SYNTHETIC_USER_MARKERS):
        return False
    content = message.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                if part.strip():
                    return True
                continue
            if isinstance(part, dict) and part.get("type") in {"text", "input_text"}:
                if isinstance(part.get("text"), str) and not part["text"].strip():
                    continue
            # Images, audio and unknown structured blocks are user input.
            return True
        return False
    return content is not None


def reanchor_current_turn_user_idx(messages: List[Any], user_message: Any) -> int:
    """Find the current user message after a compression list rebuild.

    Exact content wins.  If it was rewritten during compaction, fall back to
    the newest ordinary user row while skipping known synthetic scaffolding.
    Returns ``-1`` when no suitable user row exists.
    """

    if not isinstance(messages, list):
        return -1
    fallback = -1
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not (isinstance(message, dict) and message.get("role") == "user"):
            continue
        if message.get("content") == user_message:
            return index
        if not _is_actionable_user_row(message):
            continue
        if fallback < 0:
            fallback = index
    return fallback


def compression_made_progress(
    orig_len: int,
    new_len: int,
    orig_tokens: int,
    new_tokens: int,
) -> bool:
    """Return true when compression reduced rows or tokens materially."""

    if new_len < orig_len:
        return True
    return orig_tokens > 0 and new_tokens < orig_tokens * 0.95


_compression_made_progress = compression_made_progress


def _review_fork_first_request_pending(agent: Any) -> bool:
    """Whether a review fork must preserve its full first-request snapshot."""

    return bool(
        getattr(agent, "_review_defer_compaction_before_first_response", False)
        and not getattr(agent, "_turn_received_provider_response", False)
    )


def _compression_warrants_another_preflight_pass(
    orig_tokens: int,
    new_tokens: int,
    threshold_tokens: int,
) -> bool:
    """Require a material token reduction before another expensive pass."""

    return (
        new_tokens >= threshold_tokens
        and orig_tokens > 0
        and new_tokens < orig_tokens * 0.95
    )


def _should_run_preflight_estimate(
    messages: List[Dict[str, Any]],
    protect_first_n: int,
    protect_last_n: int,
    threshold_tokens: int,
) -> bool:
    """Decide whether the full request-token estimate is worth computing."""

    if len(messages) > protect_first_n + protect_last_n + 1:
        return True
    try:
        from agent.model_metadata import estimate_messages_tokens_rough

        return estimate_messages_tokens_rough(messages) >= threshold_tokens
    except Exception:
        # A missing optional estimator must not make a caller perform an
        # unbounded or provider-visible operation.
        return False


def _should_idle_compact(
    *,
    enabled: bool,
    idle_after_seconds: int,
    idle_gap_seconds: float,
    tokens: int,
    floor_tokens: int,
    cooldown_active: bool,
) -> bool:
    """Pure policy predicate for opt-in idle compaction."""

    if not enabled or idle_after_seconds <= 0:
        return False
    if idle_gap_seconds < idle_after_seconds or cooldown_active:
        return False
    return tokens > floor_tokens


@dataclass
class TurnContext:
    """Values a future local turn prologue may hand to the loop."""

    user_message: str
    original_user_message: Any
    messages: List[Dict[str, Any]]
    conversation_history: Optional[List[Dict[str, Any]]]
    active_system_prompt: Optional[str]
    effective_task_id: str
    turn_id: str
    current_turn_user_idx: int
    should_review_memory: bool = False
    plugin_user_context: str = ""
    ext_prefetch_cache: str = ""
    preflight_compression_blocked: bool = False


__all__ = [
    "TurnContext",
    "_compression_made_progress",
    "_compression_warrants_another_preflight_pass",
    "_review_fork_first_request_pending",
    "_should_idle_compact",
    "_should_run_preflight_estimate",
    "append_notes_to_multimodal_content",
    "clone_message_for_api",
    "compose_user_api_content",
    "consume_gateway_turn_context_notes",
    "drop_stale_api_content",
    "extract_api_content_sidecar",
    "reanchor_current_turn_user_idx",
    "substitute_api_content",
]
