"""MemoryProvider adapter for the local UnifiedMemoryGateway.

The QQ product already owns a multi-layer memory backend (STM/EPI/LTM,
workflow and wiki retrieval) behind ``UnifiedMemoryGateway``.  This adapter
provides the upstream ``MemoryProvider`` shape without making the provider
auto-active: the existing gateway lifecycle hooks remain the default writer
until a deployment explicitly selects this adapter.

Keeping activation explicit is important.  Enabling the adapter while the
``memory_maintenance`` hooks are also installed would record every turn twice.
The adapter therefore has no plugin-discovery side effect and never closes a
gateway it did not create.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Dict, List, Optional

from agent.memory_manager import build_memory_context_block, sanitize_context
from agent.memory_provider import MemoryProvider, RecallStatus, is_trivial_prompt

logger = logging.getLogger(__name__)


class BuiltinMemoryProviderAdapter(MemoryProvider):
    """Expose ``UnifiedMemoryGateway`` through the provider contract."""

    def __init__(self, gateway: Any = None) -> None:
        self._gateway = gateway
        self._session_id = ""
        self._chat_type = "dm"
        self._user_id = ""
        self._user_name = ""
        self._bot_name = "Soyo"
        self._last_recall_status: Optional[RecallStatus] = None

    @property
    def name(self) -> str:
        return "builtin"

    def is_available(self) -> bool:
        """Check imports only; availability must not create a DB or sync wiki."""
        try:
            from agent.memory.gateway import UnifiedMemoryGateway  # noqa: F401
            return True
        except Exception:
            return False

    def unavailable_reason(self) -> str:
        if self.is_available():
            return ""
        return "local UnifiedMemoryGateway is unavailable"

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        if self._gateway is None:
            from agent.memory.gateway import UnifiedMemoryGateway

            # Do not call on_session_start here: the local implementation may
            # perform a wiki sync, which is not a safe provider-init side effect.
            self._gateway = UnifiedMemoryGateway.get_instance()
        self._session_id = str(session_id or "")
        self._chat_type = str(kwargs.get("chat_type") or "dm")
        self._user_id = str(kwargs.get("user_id") or "")
        self._user_name = str(kwargs.get("user_name") or "")
        self._bot_name = str(kwargs.get("bot_name") or "Soyo")
        self._last_recall_status = None

    def system_prompt_block(self) -> str:
        gateway = self._gateway
        if gateway is None:
            return ""
        try:
            block = gateway.build_memory_prompt_block(max_chars=3000)
            if not isinstance(block, str) or not block.strip():
                return ""
            return build_memory_context_block(sanitize_context(block)[:3000])
        except Exception as exc:
            logger.debug("Builtin memory system prompt failed: %s", exc)
            return ""

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        gateway = self._gateway
        self._last_recall_status = None
        if gateway is None or is_trivial_prompt(query):
            return ""

        sid = str(session_id or self._session_id or "")
        try:
            recall_kwargs = {
                "session_id": sid or None,
                "max_chars": 4000,
            }
            try:
                recall_signature = inspect.signature(gateway.recall_structured)
                supports_chat_type = (
                    "chat_type" in recall_signature.parameters
                    or any(
                        parameter.kind == inspect.Parameter.VAR_KEYWORD
                        for parameter in recall_signature.parameters.values()
                    )
                )
            except (TypeError, ValueError):
                supports_chat_type = False
            if supports_chat_type:
                recall_kwargs["chat_type"] = self._chat_type
            structured = gateway.recall_structured(query, **recall_kwargs)
            if not isinstance(structured, dict):
                return ""
            parts: List[str] = []
            prompt = structured.get("prompt")
            if isinstance(prompt, str) and prompt.strip():
                parts.append(prompt)
            if sid:
                stm_context = gateway.get_stm_context(sid, self._chat_type)
                if isinstance(stm_context, str) and stm_context.strip():
                    parts.insert(0, stm_context)
            result = "\n\n".join(parts)
            if result.strip():
                raw_results = structured.get("results")
                count = len(raw_results) if isinstance(raw_results, list) else 0
                self._last_recall_status = RecallStatus(
                    "Unified Memory",
                    max(0, min(count, 999)),
                )
                return result
        except Exception as exc:
            logger.debug("Builtin memory prefetch failed (non-fatal): %s", exc)
        return ""

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """The local gateway has no remote prefetch queue; keep this a no-op."""

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        gateway = self._gateway
        sid = str(session_id or self._session_id or "")
        if gateway is None or not sid:
            return
        chat_type = self._chat_type or "dm"
        user_text = str(user_content or "").strip()
        assistant_text = str(assistant_content or "").strip()
        if user_text:
            gateway.process_turn(
                session_id=sid,
                role="user",
                content=user_text,
                speaker_name=self._user_name or self._user_id,
                chat_type=chat_type,
                bot_replied=bool(assistant_text),
            )
        if assistant_text:
            gateway.process_turn(
                session_id=sid,
                role="assistant",
                content=assistant_text,
                speaker_name=self._bot_name,
                chat_type=chat_type,
                bot_replied=True,
            )

    def recall_status(self) -> Optional[RecallStatus]:
        return self._last_recall_status

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        if self._gateway is None or not self._session_id:
            return
        self._gateway.on_session_end(self._session_id)
        self._last_recall_status = None

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs: Any,
    ) -> None:
        if not new_session_id:
            return
        self._session_id = str(new_session_id)
        self._last_recall_status = None

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """Return no durable checkpoint until the gateway gains one.

        The adapter intentionally stays on checkpoint API v1.  A raw STM row
        write is not enough to claim fail-closed recovery for a lossy context
        rewrite, so deployments must not infer v2 support from this adapter.
        """
        return ""

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        # The local ``memory`` tool is registered by run_agent.py and must not
        # be duplicated through this adapter.
        return []

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs: Any) -> str:
        raise NotImplementedError("BuiltinMemoryProviderAdapter exposes no extra tools")

    def shutdown(self) -> None:
        # UnifiedMemoryGateway is shared with gateway hooks; closing it here
        # would break the next request. The owning process closes its store.
        self._last_recall_status = None

    def backup_paths(self) -> List[str]:
        # SessionDB/memory-store backup ownership remains with the local
        # backup subsystem; the adapter must not broaden filesystem scope.
        return []
