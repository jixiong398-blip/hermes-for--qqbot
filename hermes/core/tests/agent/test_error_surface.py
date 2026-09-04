"""Focused tests for the stable agent error-surface contract."""

from __future__ import annotations

import pytest

from agent.error_classifier import FailoverReason
from agent.error_surface import (
    LAYER_AUTH,
    LAYER_BILLING,
    LAYER_DISK,
    LAYER_ENDPOINT,
    LAYER_GATEWAY,
    LAYER_PROVIDER,
    LAYER_RUNTIME,
    LAYER_STREAMING,
    attach_error_surface,
    build_error_surface_from_exception,
    build_error_surface_from_result,
)
from agent.errors import EmptyStreamError, MoAPresetNotFoundError, SSLConfigurationError


def _failed_result(reason: str = "", error: str = "provider exploded", **extra) -> dict:
    result = {"completed": False, "failed": True, "error": error}
    if reason:
        result["failure_reason"] = reason
    result.update(extra)
    return result


def test_healthy_results_are_untouched():
    result = {"completed": True, "final_response": "ok"}
    assert build_error_surface_from_result(result) is None
    assert attach_error_surface(result) is result
    assert build_error_surface_from_result(None) is None


@pytest.mark.parametrize(
    ("reason", "layer", "retryable"),
    [
        ("auth", LAYER_AUTH, False),
        ("auth_permanent", LAYER_AUTH, False),
        ("billing", LAYER_BILLING, False),
        ("rate_limit", LAYER_PROVIDER, True),
        ("server_error", LAYER_PROVIDER, True),
        ("format_error", LAYER_PROVIDER, False),
        ("unknown", LAYER_PROVIDER, True),
    ],
)
def test_failure_reason_maps_to_stable_layer(reason, layer, retryable):
    surface = build_error_surface_from_result(_failed_result(reason))
    assert surface["layer"] == layer
    assert surface["code"] == reason
    assert surface["retryable"] is retryable


def test_result_prefers_explicit_retryability_and_identity():
    surface = build_error_surface_from_result(
        _failed_result(
            "unknown",
            error="provider failed",
            failure_retryable=False,
        ),
        provider="openrouter",
        model="provider/model",
    )
    assert surface["retryable"] is False
    assert surface["provider"] == "openrouter"
    assert surface["model"] == "provider/model"
    assert surface["message"] == "provider failed"


def test_billing_block_wins_over_provider_reason():
    surface = build_error_surface_from_result(
        _failed_result("rate_limit", billing_block={"provider": "provider"})
    )
    assert surface["layer"] == LAYER_BILLING
    assert surface["code"] == "rate_limit"
    assert surface["retryable"] is False


def test_custom_timeout_is_endpoint_layer():
    surface = build_error_surface_from_result(
        _failed_result("timeout"), provider="custom", model="local-model"
    )
    assert surface["layer"] == LAYER_ENDPOINT
    assert surface["code"] == "timeout"

    surface = build_error_surface_from_result(
        _failed_result("timeout"), provider="anthropic"
    )
    assert surface["layer"] == LAYER_PROVIDER


def test_legacy_stream_drop_text_gets_streaming_code():
    surface = build_error_surface_from_result(
        _failed_result(error="The provider stream connection keeps dropping")
    )
    assert surface["layer"] == LAYER_STREAMING
    assert surface["code"] == "stream_drop"
    assert surface["retryable"] is True


def test_legacy_failure_defaults_to_provider_unknown():
    surface = build_error_surface_from_result(_failed_result(error="something odd"))
    assert surface["layer"] == LAYER_PROVIDER
    assert surface["code"] == "unknown"
    assert surface["retryable"] is True


def test_disk_full_wins_over_other_failure():
    surface = build_error_surface_from_result(
        _failed_result(
            "server_error", error="OSError: [Errno 28] No space left on device"
        )
    )
    assert surface["layer"] == LAYER_DISK
    assert surface["code"] == "disk_full"
    assert surface["retryable"] is False


def test_failure_reason_enum_is_normalized():
    surface = build_error_surface_from_result(
        _failed_result(FailoverReason.auth)
    )
    assert surface["layer"] == LAYER_AUTH


def test_exception_layers_and_classifier_metadata():
    gateway_surface = build_error_surface_from_exception(KeyError("history"))
    assert gateway_surface["layer"] == LAYER_GATEWAY
    assert gateway_surface["code"] == "KeyError"
    assert gateway_surface["retryable"] is True

    runtime_surface = build_error_surface_from_exception(FileNotFoundError("state"))
    assert runtime_surface["layer"] == LAYER_RUNTIME

    class FakeAPIError(Exception):
        status_code = 401

    auth_surface = build_error_surface_from_exception(
        FakeAPIError("invalid api key"), provider="openrouter", model="model"
    )
    assert auth_surface["layer"] == LAYER_AUTH
    assert auth_surface["code"] == "auth"
    assert auth_surface["provider"] == "openrouter"
    assert auth_surface["model"] == "model"


def test_nested_status_and_context_are_classified():
    class CauseError(Exception):
        def __init__(self):
            super().__init__("upstream failed")
            self.cause = {"status": "HTTP 503"}

    surface = build_error_surface_from_exception(
        CauseError(), provider="openrouter", model="model"
    )
    assert surface["layer"] == LAYER_PROVIDER
    assert surface["code"] == "overloaded"

    class ContextError(Exception):
        context = {"status_code": 401}

    surface = build_error_surface_from_exception(ContextError("unauthorized"))
    assert surface["layer"] == LAYER_AUTH

    result = attach_error_surface(
        {"failed": True, "error": "upstream unavailable", "status": {"code": 503}}
    )
    assert result["status_code"] == 503
    assert result["failure_reason"] == "overloaded"


def test_special_shared_exceptions_have_explicit_layers():
    empty_stream = build_error_surface_from_exception(
        EmptyStreamError("provider returned no events"), provider="ollama"
    )
    assert empty_stream["layer"] == LAYER_STREAMING
    assert empty_stream["code"] == "empty_stream"
    assert empty_stream["retryable"] is True

    ssl_surface = build_error_surface_from_exception(
        SSLConfigurationError("certificate bundle is invalid"), provider="custom"
    )
    assert ssl_surface["layer"] == LAYER_ENDPOINT
    assert ssl_surface["code"] == "ssl_configuration"
    assert ssl_surface["retryable"] is False

    moa_surface = build_error_surface_from_exception(
        MoAPresetNotFoundError("preset missing"), provider="moa"
    )
    assert moa_surface["layer"] == LAYER_PROVIDER
    assert moa_surface["code"] == "model_not_found"
    assert moa_surface["retryable"] is False


def test_exception_disk_full_is_non_retryable():
    surface = build_error_surface_from_exception(OSError(28, "No space left on device"))
    assert surface["layer"] == LAYER_DISK
    assert surface["code"] == "disk_full"
    assert surface["retryable"] is False


def test_exception_surface_never_raises_on_hostile_attribute():
    class Hostile(Exception):
        @property
        def status_code(self):
            raise RuntimeError("hostile attribute")

    surface = build_error_surface_from_exception(Hostile("x"))
    assert surface["layer"] == LAYER_GATEWAY


def test_attachment_copies_only_failed_results():
    result = _failed_result("rate_limit", error="rate limited")
    enriched = attach_error_surface(result, provider="openrouter", model="model")
    assert enriched is not result
    assert result.get("error_surface") is None
    assert enriched["error_surface"]["code"] == "rate_limit"


def test_attachment_preserves_classified_metadata_and_status():
    from agent.error_classifier import ClassifiedError

    result = {"failed": True, "error": "provider failed"}
    classified = ClassifiedError(
        reason=FailoverReason.server_error,
        status_code=503,
        retryable=True,
    )
    enriched = attach_error_surface(
        result,
        provider="openrouter",
        model="model",
        classified_error=classified,
    )
    assert enriched["failure_reason"] == "server_error"
    assert enriched["failure_retryable"] is True
    assert enriched["status_code"] == 503
    assert enriched["error_surface"]["code"] == "server_error"


def test_attachment_normalizes_enum_failure_reason():
    enriched = attach_error_surface(
        _failed_result(FailoverReason.auth),
    )
    assert enriched["failure_reason"] == "auth"


def test_attachment_is_fail_open_for_unusual_result_value():
    class HostileValue:
        def __str__(self):
            raise RuntimeError("hostile value")

    result = {"failed": True, "error": HostileValue()}
    enriched = attach_error_surface(result)
    assert isinstance(enriched, dict)
    assert enriched["error_surface"]["code"] == "unknown"


def test_redaction_failure_omits_untrusted_diagnostics(monkeypatch):
    import agent.redact

    def _redaction_failure(*args, **kwargs):
        raise RuntimeError("redactor unavailable")

    monkeypatch.setattr(agent.redact, "redact_sensitive_text", _redaction_failure)
    surface = build_error_surface_from_result(
        _failed_result(error="Authorization: sk-secret-value-that-must-not-leak"),
        provider="provider-with-secret",
        model="model-with-secret",
    )
    assert len(surface["code"]) <= 96
    assert "message" not in surface
    assert "provider" not in surface
    assert "model" not in surface


def test_surface_identifiers_are_bounded():
    surface = build_error_surface_from_result(
        _failed_result("x" * 500, error="details"),
        provider="p" * 500,
        model="m" * 500,
    )
    assert len(surface["code"]) <= 96
    assert len(surface["provider"]) <= 128
    assert len(surface["model"]) <= 128


def test_stream_marker_uses_word_boundaries():
    assessment = build_error_surface_from_result(
        _failed_result(error="assessment failed")
    )
    assert assessment["layer"] == LAYER_PROVIDER
    assert assessment["code"] == "unknown"


def test_empty_sentinel_error_text_is_sanitized():
    result = attach_error_surface(
        {
            "completed": True,
            "final_response": "(empty)",
            "error": "Authorization: Bearer sk-1234567890abcdef",
        }
    )
    assert "sk-1234567890abcdef" not in result["error"]
    assert result["final_response"] == "(empty)"


def test_shared_exception_types_are_stable():
    assert issubclass(SSLConfigurationError, Exception)
    assert issubclass(EmptyStreamError, RuntimeError)
    assert issubclass(MoAPresetNotFoundError, ValueError)
