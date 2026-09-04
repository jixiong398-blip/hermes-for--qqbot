"""Stable descriptors for errors crossing the agent/gateway boundary.

The agent already has a detailed provider failure taxonomy in
``agent.error_classifier``.  This module translates that internal taxonomy
into a small UI-facing descriptor without changing retry policy or user-facing
error text::

    {"layer": "provider", "code": "rate_limit", "retryable": True,
     "provider": "openrouter", "model": "provider/model",
     "message": "rate limit exceeded"}

All public helpers fail open.  Error reporting must never replace the error it
is trying to describe, especially while running on Windows where SDK and
filesystem objects can expose surprising attributes.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Wire-level layer names.  Keep these values stable for gateway/UI clients.
LAYER_PROVIDER = "provider"
LAYER_ENDPOINT = "endpoint"
LAYER_STREAMING = "streaming"
LAYER_AUTH = "auth"
LAYER_BILLING = "billing"
LAYER_GATEWAY = "gateway"
LAYER_RUNTIME = "runtime"
LAYER_DISK = "disk"

_MAX_PROVIDER_MODEL_LENGTH = 128
_MAX_CODE_LENGTH = 96
_MAX_LAYER_LENGTH = 32
_MAX_STATUS_DEPTH = 4

_REASON_TO_LAYER = {
    "auth": LAYER_AUTH,
    "auth_permanent": LAYER_AUTH,
    "billing": LAYER_BILLING,
    "billing_unverified": LAYER_BILLING,
    "session_persistence_failed:disk": LAYER_DISK,
    "empty_stream": LAYER_STREAMING,
    "empty_response": LAYER_PROVIDER,
    "ssl_configuration": LAYER_ENDPOINT,
}

_TRANSPORT_REASONS = {
    "timeout",
    "ssl_cert_verification",
}

# Used only when a legacy result has a reason but no classifier verdict.
_NON_RETRYABLE_REASONS = {
    "auth",
    "auth_permanent",
    "billing",
    "billing_unverified",
    "content_policy_blocked",
    "provider_policy_blocked",
    "model_not_found",
    "format_error",
    "ssl_cert_verification",
    "session_persistence_failed:disk",
    "empty_response",
    "ssl_configuration",
}

_CUSTOM_ENDPOINT_PROVIDERS = {
    "custom",
    "local",
    "llama.cpp",
    "llamacpp",
    "ollama",
    "lmstudio",
    "vllm",
}

# Use word boundaries for the short ``sse`` marker.  A plain substring check
# would classify unrelated words such as ``assessment`` as stream failures.
_STREAM_DROP_RE = re.compile(
    r"(?:"
    r"\bstream(?:ing)?\s+(?:connection|error|ended|closed)\b|"
    r"\bpeer\s+closed\s+connection\b|"
    r"\bincomplete\s+chunked\s+read\b|"
    r"\bconnection\s+(?:broken|reset|closed)\b|"
    r"\bnetwork\s+connection\s+(?:lost|broken|closed)\b|"
    r"\bstream\s+ended\s+prematurely\b|"
    r"\bmid[- ]stream\b|"
    r"\bsse\b"
    r")",
    re.IGNORECASE,
)

_API_EXC_MODULE_PREFIXES = (
    "openai",
    "httpx",
    "httpcore",
    "anthropic",
    "botocore",
    "boto3",
    "google",
    "grpc",
    "requests",
    "aiohttp",
    "ssl",
    "socket",
    "urllib",
)

_DISK_FULL_FRAGMENTS = (
    "no space left on device",
    "database or disk is full",
    "not enough space",
    "disk full",
    "enospc",
    "errno 28",
)

_RUNTIME_EXCEPTION_NAMES = {
    "FileNotFoundError",
    "ImportError",
    "ModuleNotFoundError",
    "PermissionError",
}
_ERROR_EXIT_PREFIXES = (
    "error_",
    "local_processing_error",
    "repeated_outer_errors",
    "all_retries_exhausted",
    "max_retries_exhausted",
)

_STATUS_KEYS = (
    "status_code",
    "status",
    "http_status",
    "http_status_code",
    "statusCode",
    "code",
)
_STATUS_NESTED_KEYS = (
    "cause",
    "__cause__",
    "context",
    "__context__",
    "response",
    "error",
    "exception",
)
_STATUS_TEXT_RE = re.compile(
    r"^(?:http\s*)?([1-5]\d{2})\b|"
    r"\b(?:status(?:_code)?|http(?:_status|_status_code)?|code)"
    r"\s*[:=]?\s*([1-5]\d{2})\b",
    re.IGNORECASE,
)


def _safe_text(value: Any, *, limit: int = 500) -> str:
    """Convert arbitrary diagnostic data to bounded, single-line text."""
    try:
        text = str(value or "").strip()
    except Exception:  # noqa: BLE001 - diagnostics must never raise
        return ""
    if not text:
        return ""
    # Diagnostics cross a trust boundary.  If the mandatory redactor cannot
    # be loaded or fails, omit the text rather than returning a raw secret.
    try:
        from agent.redact import redact_sensitive_text

        text = redact_sensitive_text(text, force=True)
    except Exception:  # noqa: BLE001 - fail closed at the safety boundary
        return ""
    if not isinstance(text, str):
        return ""
    try:
        return " ".join(str(text).split())[:limit]
    except Exception:  # noqa: BLE001
        return ""


def _safe_value(value: Any, *, limit: int = _MAX_CODE_LENGTH) -> str:
    try:
        return str(value or "").strip()[:limit]
    except Exception:  # noqa: BLE001
        return ""


def _safe_identifier(value: Any, *, limit: int) -> str:
    """Return a bounded, redacted identifier or an empty string."""
    return _safe_text(value, limit=limit)


def _safe_get(obj: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name, default)
    except Exception:  # noqa: BLE001 - hostile SDK objects are possible
        return default


def _coerce_status_code(value: Any) -> Optional[int]:
    """Coerce a status-like value without accepting arbitrary large data."""
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if 100 <= value < 600 else None
        if isinstance(value, str):
            match = _STATUS_TEXT_RE.search(value.strip()[:200])
            if match:
                raw = match.group(1) or match.group(2)
                code = int(raw)
                return code if 100 <= code < 600 else None
    except Exception:  # noqa: BLE001 - hostile SDK values are possible
        return None
    return None


def _extract_status_code(
    value: Any,
    *,
    _seen: Optional[set[int]] = None,
    _depth: int = 0,
) -> Optional[int]:
    """Find status codes on ``status``/``cause``/``context`` chains safely."""
    if value is None or _depth > _MAX_STATUS_DEPTH:
        return None

    direct = _coerce_status_code(value)
    if direct is not None:
        return direct

    if _seen is None:
        _seen = set()
    try:
        marker = id(value)
        if marker in _seen:
            return None
        _seen.add(marker)
    except Exception:  # pragma: no cover - id() is defensive only
        pass

    if isinstance(value, dict):
        for key in _STATUS_KEYS:
            try:
                if key in value:
                    candidate = value.get(key)
                    code = _coerce_status_code(candidate)
                    if code is None and candidate is not value:
                        code = _extract_status_code(
                            candidate, _seen=_seen, _depth=_depth + 1
                        )
                    if code is not None:
                        return code
            except Exception:
                continue
        for key in _STATUS_NESTED_KEYS:
            try:
                if key in value:
                    code = _extract_status_code(
                        value.get(key), _seen=_seen, _depth=_depth + 1
                    )
                    if code is not None:
                        return code
            except Exception:
                continue
        return None

    for name in _STATUS_KEYS:
        candidate = _safe_get(value, name)
        code = _coerce_status_code(candidate)
        if code is None and candidate is not None and candidate is not value:
            code = _extract_status_code(
                candidate, _seen=_seen, _depth=_depth + 1
            )
        if code is not None:
            return code
    for name in _STATUS_NESTED_KEYS:
        candidate = _safe_get(value, name)
        if candidate is None or candidate is value:
            continue
        code = _extract_status_code(
            candidate, _seen=_seen, _depth=_depth + 1
        )
        if code is not None:
            return code
    return None


def _is_custom_endpoint(provider: Optional[str]) -> bool:
    p = _safe_identifier(provider, limit=_MAX_PROVIDER_MODEL_LENGTH).lower()
    return p in _CUSTOM_ENDPOINT_PROVIDERS or p.startswith("custom:")


def _looks_like_stream_drop(message: str) -> bool:
    lower = _safe_text(message).lower()
    return bool(_STREAM_DROP_RE.search(lower))


def _looks_like_disk_full(value: Any) -> bool:
    """Detect ENOSPC without requiring a newer ``hermes_state`` helper."""
    try:
        errno_value = _safe_get(value, "errno")
        if errno_value == 28:
            return True
    except Exception:  # noqa: BLE001
        pass
    message = _safe_text(value).lower()
    return any(fragment in message for fragment in _DISK_FULL_FRAGMENTS)


def _is_disk_full(value: Any) -> bool:
    try:
        from hermes_state import is_disk_full_error

        if is_disk_full_error(value):
            return True
    except Exception:  # noqa: BLE001 - older forks may not provide it
        pass
    return _looks_like_disk_full(value)


def _normalize_reason(value: Any) -> str:
    try:
        enum_value = getattr(value, "value", value)
    except Exception:  # noqa: BLE001
        enum_value = value
    return _safe_identifier(enum_value, limit=_MAX_CODE_LENGTH)


def _surface(
    layer: str,
    code: str,
    retryable: bool,
    provider: Any = "",
    model: Any = "",
    message: Any = "",
) -> dict:
    """Build a descriptor using only bounded, serializable values."""
    out = {
        "layer": _safe_value(layer, limit=_MAX_LAYER_LENGTH) or LAYER_GATEWAY,
        "code": _safe_identifier(code, limit=_MAX_CODE_LENGTH) or "unknown",
        "retryable": bool(retryable),
    }
    provider_text = _safe_identifier(
        provider, limit=_MAX_PROVIDER_MODEL_LENGTH
    )
    model_text = _safe_identifier(model, limit=_MAX_PROVIDER_MODEL_LENGTH)
    message_text = _safe_text(message)
    if provider_text:
        out["provider"] = provider_text
    if model_text:
        out["model"] = model_text
    if message_text:
        out["message"] = message_text
    return out


def sanitize_error_message(value: Any, *, fallback: str = "") -> str:
    """Return redacted user-facing error text, or a safe fallback."""
    return _safe_text(value) or fallback


class _ResultError(Exception):
    """Small classifier input carrying legacy-result status metadata."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


def _classify_result_message(
    error_text: str,
    *,
    provider: str,
    model: str,
    status_code: Optional[int] = None,
) -> tuple[str, Optional[bool]]:
    """Best-effort classification for pre-taxonomy legacy result dictionaries."""
    if not error_text and status_code is None:
        return "", None
    try:
        from agent.error_classifier import classify_api_error

        classified = classify_api_error(
            _ResultError(error_text, status_code),
            provider=provider,
            model=model,
        )
        reason = _normalize_reason(classified.reason)
        return reason, bool(classified.retryable)
    except Exception:  # noqa: BLE001 - a legacy result must remain usable
        return "", None


def _failure_metadata(
    result: dict,
    *,
    provider: str,
    model: str,
    classified_error: Any = None,
) -> tuple[str, Optional[bool]]:
    """Resolve stable failure metadata without mutating the input mapping."""
    reason = _normalize_reason(result.get("failure_reason"))
    retryable = result.get("failure_retryable")
    if not isinstance(retryable, bool):
        retryable = None

    if result.get("billing_block") and not reason:
        return "billing", False

    error_text = _safe_text(result.get("error"))
    status_code = _extract_status_code(result)
    inferred_reason = ""
    inferred_retryable: Optional[bool] = None
    if not reason and classified_error is not None:
        inferred_reason = _normalize_reason(
            _safe_get(classified_error, "reason")
        )
        classified_retryable = _safe_get(classified_error, "retryable")
        if isinstance(classified_retryable, bool):
            inferred_retryable = classified_retryable
        status_code = status_code or _coerce_status_code(
            _safe_get(classified_error, "status_code")
        )
    if not reason and not inferred_reason:
        inferred_reason, inferred_retryable = _classify_result_message(
            error_text,
            provider=provider,
            model=model,
            status_code=status_code,
        )
    reason = reason or inferred_reason
    if not reason and (
        result.get("failed")
        or result.get("final_response") == "(empty)"
        or result.get("error_surface")
    ):
        reason = "unknown"
    if retryable is None:
        retryable = inferred_retryable
    if retryable is None and reason:
        retryable = reason not in _NON_RETRYABLE_REASONS
    return reason, retryable


def build_error_surface_from_result(
    result: Any,
    provider: str = "",
    model: str = "",
) -> Optional[dict]:
    """Build a descriptor from a returned agent result, or ``None`` if healthy.

    Both current and future result shapes are accepted.  Current fork results
    sometimes omit ``failure_reason``; in that case the existing classifier is
    asked to classify the bounded error string and the descriptor falls back to
    ``provider/unknown`` if classification is unavailable.
    """
    try:
        if not isinstance(result, dict):
            return None

        error_text = _safe_text(result.get("error"))
        reason = _normalize_reason(result.get("failure_reason"))
        if not error_text and not reason and not result.get("failed"):
            return None

        result_provider = _safe_identifier(
            provider, limit=_MAX_PROVIDER_MODEL_LENGTH
        ) or _safe_identifier(
            result.get("provider"), limit=_MAX_PROVIDER_MODEL_LENGTH
        )
        result_model = _safe_identifier(
            model, limit=_MAX_PROVIDER_MODEL_LENGTH
        ) or _safe_identifier(
            result.get("model"), limit=_MAX_PROVIDER_MODEL_LENGTH
        )

        if _is_disk_full(result) or _is_disk_full(error_text):
            return _surface(
                LAYER_DISK,
                "disk_full",
                False,
                result_provider,
                result_model,
                error_text,
            )

        if result.get("billing_block") or reason in {
            "billing",
            "billing_unverified",
        }:
            return _surface(
                LAYER_BILLING,
                reason or "billing",
                False,
                result_provider,
                result_model,
                error_text,
            )

        inferred_retryable: Optional[bool] = None
        if not reason:
            reason, inferred_retryable = _classify_result_message(
                error_text,
                provider=result_provider,
                model=result_model,
                status_code=_extract_status_code(result),
            )

            # Legacy gateway results often contain only the retry exhaustion
            # text.  The provider classifier quite reasonably returns
            # ``unknown`` for a generic wrapper exception, but a recognizable
            # stream-drop message still has a more useful stable code.
            if reason == "unknown" and _looks_like_stream_drop(error_text):
                reason = ""

        if not reason:
            if _looks_like_stream_drop(error_text):
                layer, code = LAYER_STREAMING, "stream_drop"
            else:
                layer, code = LAYER_PROVIDER, "unknown"
            return _surface(
                layer,
                code,
                True,
                result_provider,
                result_model,
                error_text,
            )

        # Keep ``failure_reason`` as the classifier's canonical value while
        # allowing a more specific UI code for recognizable legacy text.
        surface_reason = reason
        if surface_reason == "unknown" and _looks_like_stream_drop(error_text):
            surface_reason = ""

        if not surface_reason:
            return _surface(
                LAYER_STREAMING,
                "stream_drop",
                True,
                result_provider,
                result_model,
                error_text,
            )

        if surface_reason == "session_persistence_failed:disk":
            layer = LAYER_DISK
        else:
            layer = _REASON_TO_LAYER.get(surface_reason)
            if layer is None:
                if (
                    surface_reason in _TRANSPORT_REASONS
                    and _is_custom_endpoint(result_provider)
                ):
                    layer = LAYER_ENDPOINT
                elif _looks_like_stream_drop(error_text):
                    layer = LAYER_STREAMING
                else:
                    layer = LAYER_PROVIDER

        retryable = result.get("failure_retryable")
        if not isinstance(retryable, bool):
            retryable = (
                inferred_retryable
                if inferred_retryable is not None
                else surface_reason not in _NON_RETRYABLE_REASONS
            )
        return _surface(
            layer,
            surface_reason,
            retryable,
            result_provider,
            result_model,
            error_text,
        )
    except Exception:  # pragma: no cover - error reporting must fail open
        logger.debug("error_surface: result classification failed", exc_info=True)
        return None


def build_error_surface_from_exception(
    exc: BaseException,
    provider: str = "",
    model: str = "",
) -> Optional[dict]:
    """Build a descriptor for an exception without ever masking that exception."""
    try:
        exc_type = _safe_value(type(exc).__name__)
        message = _safe_text(exc) or exc_type

        # These types are intentionally local and do not belong to an SDK
        # module, so classify them before the generic gateway/runtime split.
        try:
            from agent.errors import (
                EmptyStreamError,
                MoAPresetNotFoundError,
                SSLConfigurationError,
            )

            if isinstance(exc, EmptyStreamError):
                return _surface(
                    LAYER_STREAMING,
                    "empty_stream",
                    True,
                    provider,
                    model,
                    message,
                )
            if isinstance(exc, SSLConfigurationError):
                return _surface(
                    LAYER_ENDPOINT,
                    "ssl_configuration",
                    False,
                    provider,
                    model,
                    message,
                )
            if isinstance(exc, MoAPresetNotFoundError):
                return _surface(
                    LAYER_PROVIDER,
                    "model_not_found",
                    False,
                    provider,
                    model,
                    message,
                )
        except Exception:  # pragma: no cover - compatibility import guard
            pass

        if _is_disk_full(exc) or _is_disk_full(message):
            return _surface(
                LAYER_DISK, "disk_full", False, provider, model, message
            )

        exc_module = _safe_value(_safe_get(type(exc), "__module__", ""))
        status_code = _extract_status_code(exc)
        api_like = (
            exc_module.split(".")[0] in _API_EXC_MODULE_PREFIXES
            or status_code is not None
        )

        if not api_like or not isinstance(exc, Exception):
            layer = (
                LAYER_RUNTIME
                if exc_type in _RUNTIME_EXCEPTION_NAMES
                else LAYER_GATEWAY
            )
            return _surface(layer, exc_type or "Exception", True, provider, model, message)

        try:
            from agent.error_classifier import classify_api_error

            classifier_input = exc
            if status_code is not None:
                # Current classifier versions understand ``status_code`` and
                # ``status`` but older SDK wrappers expose a nested ``cause``
                # or ``context`` only.  Carry the safely extracted code on a
                # tiny wrapper without mutating a provider exception.
                classifier_input = _StatusCarrier(exc, message, status_code)
            classified = classify_api_error(
                classifier_input, provider=provider, model=model
            )
            reason = _normalize_reason(classified.reason)
            surface = build_error_surface_from_result(
                {
                    "error": message,
                    "failure_reason": reason,
                    "failure_retryable": bool(classified.retryable),
                    "status_code": status_code,
                },
                provider=provider,
                model=model,
            )
            if surface is not None:
                return surface
        except Exception:  # noqa: BLE001 - fallback below is intentional
            logger.debug("error_surface: exception classifier failed", exc_info=True)

        return _surface(
            LAYER_PROVIDER, "unknown", True, provider, model, message
        )
    except Exception:  # pragma: no cover - error reporting must fail open
        logger.debug("error_surface: exception classification failed", exc_info=True)
        return None


class _StatusCarrier(Exception):
    """Preserve a redacted message while exposing a nested status code."""

    def __init__(
        self,
        original: BaseException,
        message: str,
        status_code: int,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        body = _safe_get(original, "body")
        if isinstance(body, dict):
            self.body = body
        response = _safe_get(original, "response")
        if response is not None:
            self.response = response


def attach_error_surface(
    result: Any,
    *,
    provider: str = "",
    model: str = "",
    classified_error: Any = None,
) -> Any:
    """Return ``result`` with failure metadata and an advisory descriptor.

    The returned mapping is a shallow copy.  Existing callers can continue to
    reuse their result object, while gateway/API boundaries receive the
    machine-readable fields when an older return path omitted them.
    """
    try:
        if not isinstance(result, dict):
            return result

        enriched = dict(result)
        changed = False

        # Keep the existing user-facing sentinel intact while marking the
        # turn as failed for gateway retention and retry analytics.
        if enriched.get("final_response") == "(empty)":
            if not enriched.get("failed"):
                enriched["failed"] = True
                changed = True
            if not _normalize_reason(enriched.get("failure_reason")):
                enriched["failure_reason"] = "empty_response"
                changed = True
            if not isinstance(enriched.get("failure_retryable"), bool):
                enriched["failure_retryable"] = False
                changed = True
            _empty_error = _safe_text(enriched.get("error"))
            if _empty_error:
                if enriched.get("error") != _empty_error:
                    enriched["error"] = _empty_error
                    changed = True
            else:
                enriched["error"] = "Model returned no content after all retries."
                changed = True

        # Legacy terminal dictionaries sometimes carry only ``error`` or a
        # billing marker.  Normalize those into the same failed-result shape
        # used by newer turn finalizers.
        _exit_reason = _safe_value(enriched.get("turn_exit_reason"))
        _terminal_error_exit = _exit_reason.startswith(_ERROR_EXIT_PREFIXES)
        if (
            (enriched.get("error") or enriched.get("failure_reason")
             or enriched.get("billing_block") or _terminal_error_exit)
            and not enriched.get("failed")
        ):
            enriched["failed"] = True
            changed = True
        if _terminal_error_exit and not enriched.get("error"):
            enriched["error"] = _exit_reason
            changed = True

        is_error = bool(
            enriched.get("failed")
            or enriched.get("error")
            or enriched.get("failure_reason")
            or enriched.get("error_surface")
        )
        if not is_error:
            return result

        result_provider = _safe_identifier(
            provider, limit=_MAX_PROVIDER_MODEL_LENGTH
        ) or _safe_identifier(
            enriched.get("provider"), limit=_MAX_PROVIDER_MODEL_LENGTH
        )
        result_model = _safe_identifier(
            model, limit=_MAX_PROVIDER_MODEL_LENGTH
        ) or _safe_identifier(
            enriched.get("model"), limit=_MAX_PROVIDER_MODEL_LENGTH
        )
        reason, retryable = _failure_metadata(
            enriched,
            provider=result_provider,
            model=result_model,
            classified_error=classified_error,
        )
        normalized_existing_reason = _normalize_reason(
            enriched.get("failure_reason")
        )
        if reason and (
            not isinstance(enriched.get("failure_reason"), str)
            or normalized_existing_reason != reason
        ):
            enriched["failure_reason"] = reason
            changed = True
        if retryable is not None and not isinstance(
            enriched.get("failure_retryable"), bool
        ):
            enriched["failure_retryable"] = bool(retryable)
            changed = True

        result_status = _coerce_status_code(enriched.get("status_code"))
        if result_status is None:
            result_status = _extract_status_code(enriched)
        if result_status is None and classified_error is not None:
            result_status = _coerce_status_code(
                _safe_get(classified_error, "status_code")
            )
        if result_status is not None and enriched.get("status_code") != result_status:
            enriched["status_code"] = result_status
            changed = True

        # Preserve an upstream-provided surface verbatim.  Otherwise classify
        # the metadata-enriched result so legacy paths get the same contract.
        if not enriched.get("error_surface"):
            surface = build_error_surface_from_result(
                enriched,
                provider=result_provider,
                model=result_model,
            )
            if surface is not None:
                enriched["error_surface"] = surface
                changed = True

        return enriched if changed else result
    except Exception:  # pragma: no cover - preserve the original result
        logger.debug("error_surface: result attachment failed", exc_info=True)
        return result
