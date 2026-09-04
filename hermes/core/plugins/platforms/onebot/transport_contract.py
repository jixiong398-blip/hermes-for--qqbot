"""Offline contracts for the OneBot/NapCat transport boundary.

The adapter owns socket and HTTP I/O.  This module keeps endpoint parsing,
response classification, handshake/status validation, and media limits pure so
they can be tested without opening a NapCat port or contacting QQ.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass, replace
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit


MAX_ENDPOINT_URL_CHARS = 2_048
MAX_MEDIA_DOWNLOAD_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class OneBotEndpoint:
    """Validated endpoint metadata used by the adapter and diagnostics."""

    kind: str
    url: str
    scheme: str
    host: str
    port: int
    is_loopback: bool


class OneBotEndpointError(ValueError):
    """A configuration error that is safe to expose at the adapter boundary."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.retryable = False
        super().__init__(message)


@dataclass(frozen=True)
class OneBotTransportDescriptor:
    """Stable, bounded classification for a transport or protocol outcome."""

    layer: str
    code: str
    retryable: bool
    status_code: Optional[int] = None
    message: str = ""


class OneBotTransportError(RuntimeError):
    """Exception carrying a safe descriptor for an HTTP/WS boundary failure."""

    def __init__(self, descriptor: OneBotTransportDescriptor) -> None:
        self.descriptor = descriptor
        super().__init__(descriptor.message or descriptor.code)


@dataclass(frozen=True)
class OneBotReceipt:
    """Validated OneBot action response and optional message id."""

    ok: bool
    status: str
    retcode: Optional[int]
    message_id: Optional[str]
    descriptor: OneBotTransportDescriptor


@dataclass(frozen=True)
class OneBotHandshake:
    """Offline interpretation of a OneBot auth/handshake message."""

    state: str
    authenticated: bool
    descriptor: OneBotTransportDescriptor


@dataclass(frozen=True)
class OneBotHealthStatus:
    """Offline interpretation of a OneBot status response."""

    ok: bool
    online: Optional[bool]
    good: Optional[bool]
    descriptor: OneBotTransportDescriptor


def _loopback_host(host: str) -> bool:
    normalized = str(host or "").strip().lower().rstrip(".")
    if normalized in {"localhost", "localhost.localdomain", "ip6-localhost"}:
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def _endpoint_error(code: str = "invalid_endpoint") -> OneBotEndpointError:
    messages = {
        "invalid_endpoint": "OneBot endpoint configuration is invalid",
        "endpoint_credentials": "OneBot endpoint must not embed credentials",
        "endpoint_query": "OneBot endpoint must not contain a query or fragment",
        "unsupported_scheme": "OneBot endpoint scheme is not supported",
        "invalid_port": "OneBot endpoint port is invalid",
    }
    return OneBotEndpointError(code, messages.get(code, messages["invalid_endpoint"]))


def parse_onebot_endpoint(raw_url: Any, *, kind: str) -> OneBotEndpoint:
    """Validate a WS or HTTP endpoint without DNS or socket access."""
    if kind not in {"ws", "http"}:
        raise ValueError("kind must be 'ws' or 'http'")
    if not isinstance(raw_url, str):
        raise _endpoint_error()
    value = raw_url.strip()
    if not value or len(value) > MAX_ENDPOINT_URL_CHARS:
        raise _endpoint_error()
    if any(ord(char) < 32 for char in value):
        raise _endpoint_error()

    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        allowed = {"ws", "wss"} if kind == "ws" else {"http", "https"}
        if scheme not in allowed:
            raise _endpoint_error("unsupported_scheme")
        if parsed.username is not None or parsed.password is not None:
            raise _endpoint_error("endpoint_credentials")
        if parsed.query or parsed.fragment:
            raise _endpoint_error("endpoint_query")
        host = parsed.hostname
        if not host:
            raise _endpoint_error()
        try:
            parsed_port = parsed.port
        except ValueError as error:
            raise _endpoint_error("invalid_port") from error
    except OneBotEndpointError:
        raise
    except (TypeError, ValueError) as error:
        raise _endpoint_error() from error

    default_port = {
        "ws": 80,
        "wss": 443,
        "http": 80,
        "https": 443,
    }[scheme]
    port = default_port if parsed_port is None else parsed_port
    if not 1 <= port <= 65_535:
        raise _endpoint_error("invalid_port")

    # Rebuild from parsed components so credentials and query fragments can
    # never be accidentally carried into a client configuration.
    canonical = urlunsplit((scheme, parsed.netloc, parsed.path, "", ""))
    return OneBotEndpoint(
        kind=kind,
        url=canonical,
        scheme=scheme,
        host=host.lower().rstrip("."),
        port=port,
        is_loopback=_loopback_host(host),
    )


def _format_host(host: str) -> str:
    try:
        if isinstance(ipaddress.ip_address(host), ipaddress.IPv6Address):
            return f"[{host}]"
    except ValueError:
        pass
    return host


def derive_onebot_http_url(ws_url: Any) -> str:
    """Derive the NapCat HTTP base from a WS URL without network access.

    NapCat's conventional pair is WS ``:3001`` and HTTP ``:3000``.  Custom
    explicit ports remain unchanged so existing remote deployments can still
    supply a paired endpoint when needed.
    """
    endpoint = parse_onebot_endpoint(ws_url, kind="ws")
    parsed = urlsplit(endpoint.url)
    scheme = "https" if endpoint.scheme == "wss" else "http"
    if parsed.port is None:
        port = 443 if scheme == "https" else 3000
    elif endpoint.port == 3001:
        port = 3000
    else:
        port = endpoint.port
    netloc = f"{_format_host(endpoint.host)}:{port}"
    return urlunsplit((scheme, netloc, "", "", ""))


def validate_onebot_endpoints(
    ws_url: Any,
    http_url: Any = None,
) -> tuple[OneBotEndpoint, OneBotEndpoint]:
    """Validate WS/HTTP pair and derive HTTP when it is omitted."""
    ws_endpoint = parse_onebot_endpoint(ws_url, kind="ws")
    resolved_http = derive_onebot_http_url(ws_url) if not http_url else http_url
    http_endpoint = parse_onebot_endpoint(resolved_http, kind="http")
    return ws_endpoint, http_endpoint


def _status_code_from(value: Any) -> Optional[int]:
    try:
        if isinstance(value, bool):
            return None
        candidate = getattr(value, "status_code", value)
        if isinstance(candidate, str):
            candidate = int(candidate.strip())
        if isinstance(candidate, int) and 100 <= candidate < 600:
            return candidate
    except (TypeError, ValueError, AttributeError):
        return None
    return None


def classify_http_status(
    status_code: Any,
    *,
    operation: str = "action",
) -> OneBotTransportDescriptor:
    """Classify an HTTP status without retaining response body text."""
    status = _status_code_from(status_code)
    if status is None:
        return OneBotTransportDescriptor(
            layer="protocol",
            code="invalid_http_status",
            retryable=False,
            message="OneBot returned an invalid HTTP status",
        )
    if 200 <= status < 300:
        return OneBotTransportDescriptor(
            layer="protocol", code="http_ok", retryable=False, status_code=status
        )
    if status in {401, 403}:
        return OneBotTransportDescriptor(
            layer="auth",
            code="http_auth_failed",
            retryable=False,
            status_code=status,
            message="OneBot HTTP authentication was rejected",
        )
    if status == 429:
        return OneBotTransportDescriptor(
            layer="endpoint",
            code="http_rate_limited",
            retryable=True,
            status_code=status,
            message="OneBot HTTP endpoint is rate limited",
        )
    if status == 408:
        retryable = operation not in {"send", "media_download"}
        return OneBotTransportDescriptor(
            layer="connection",
            code="http_timeout",
            retryable=retryable,
            status_code=status,
            message="OneBot HTTP request timed out",
        )
    if 500 <= status < 600:
        return OneBotTransportDescriptor(
            layer="endpoint",
            code="http_server_error",
            retryable=True,
            status_code=status,
            message="OneBot HTTP service returned a server error",
        )
    if 400 <= status < 500:
        return OneBotTransportDescriptor(
            layer="protocol",
            code="http_client_error",
            retryable=False,
            status_code=status,
            message="OneBot HTTP request was rejected",
        )
    return OneBotTransportDescriptor(
        layer="protocol",
        code="http_status_unexpected",
        retryable=False,
        status_code=status,
        message="OneBot returned an unexpected HTTP status",
    )


def classify_transport_exception(
    error: BaseException,
    *,
    operation: str = "action",
) -> OneBotTransportDescriptor:
    """Classify transport exceptions with fixed, secret-free messages."""
    if isinstance(error, OneBotTransportError):
        return error.descriptor

    status = _status_code_from(getattr(error, "response", None))
    if status is not None:
        return classify_http_status(status, operation=operation)

    name = type(error).__name__.lower()
    text = str(error).lower()
    if "ssl" in name or "certificate" in name or "tls" in text:
        return OneBotTransportDescriptor(
            layer="endpoint",
            code="tls_error",
            retryable=False,
            message="OneBot TLS configuration or verification failed",
        )
    if isinstance(error, (json.JSONDecodeError,)) or "json" in name and "decode" in name:
        return OneBotTransportDescriptor(
            layer="protocol",
            code="invalid_json",
            retryable=False,
            message="OneBot returned invalid JSON",
        )
    if operation == "ws_listen":
        return OneBotTransportDescriptor(
            layer="endpoint",
            code="ws_bind_failed",
            retryable=False,
            message="OneBot reverse WebSocket listener could not bind",
        )
    if "connecttimeout" in name or "connect timeout" in text:
        return OneBotTransportDescriptor(
            layer="connection",
            code="connect_timeout",
            retryable=True,
            message="OneBot endpoint connection timed out",
        )
    if "timeout" in name or "timed out" in text:
        retryable = operation not in {"send", "media_download"}
        return OneBotTransportDescriptor(
            layer="connection",
            code="request_timeout",
            retryable=retryable,
            message="OneBot transport request timed out",
        )
    if any(
        marker in name or marker in text
        for marker in (
            "connectionrefused",
            "connectionerror",
            "connectionreset",
            "broken pipe",
            "remotedisconnected",
            "network is unreachable",
        )
    ):
        return OneBotTransportDescriptor(
            layer="connection",
            code="connection_error",
            retryable=True,
            message="OneBot transport connection failed",
        )
    if isinstance(error, (ValueError,)) and "url" in text:
        return OneBotTransportDescriptor(
            layer="endpoint",
            code="invalid_endpoint",
            retryable=False,
            message="OneBot endpoint configuration is invalid",
        )
    return OneBotTransportDescriptor(
        layer="runtime",
        code="transport_error",
        retryable=False,
        message="OneBot transport operation failed",
    )


def parse_onebot_receipt(
    payload: Any,
    *,
    require_message_id: bool = False,
    operation: str = "action",
) -> OneBotReceipt:
    """Validate a OneBot response envelope and optional send receipt."""
    if not isinstance(payload, Mapping):
        descriptor = OneBotTransportDescriptor(
            layer="protocol",
            code="invalid_response",
            retryable=False,
            message="OneBot response was not an object",
        )
        return OneBotReceipt(False, "", None, None, descriptor)

    status = str(payload.get("status") or "ok").strip().lower()
    raw_retcode = payload.get("retcode", 0)
    try:
        if isinstance(raw_retcode, bool):
            raise ValueError
        retcode = int(raw_retcode)
    except (TypeError, ValueError):
        descriptor = OneBotTransportDescriptor(
            layer="protocol",
            code="invalid_retcode",
            retryable=False,
            message="OneBot response retcode was invalid",
        )
        return OneBotReceipt(False, status, None, None, descriptor)

    if retcode != 0:
        descriptor = OneBotTransportDescriptor(
            layer="protocol",
            code="onebot_retcode",
            retryable=False,
            message="OneBot action returned a non-zero retcode",
        )
        return OneBotReceipt(False, status, retcode, None, descriptor)
    if status not in {"ok", "async"}:
        descriptor = OneBotTransportDescriptor(
            layer="protocol",
            code="onebot_status_error",
            retryable=False,
            message="OneBot response status was not successful",
        )
        return OneBotReceipt(False, status, retcode, None, descriptor)

    data = payload.get("data")
    message_id: Optional[str] = None
    if isinstance(data, Mapping):
        raw_message_id = data.get("message_id")
        if raw_message_id is not None:
            candidate = str(raw_message_id).strip()
            if candidate:
                message_id = candidate[:160]
    if require_message_id and not message_id:
        descriptor = OneBotTransportDescriptor(
            layer="protocol",
            code="missing_receipt",
            retryable=False,
            message="OneBot send response did not include a message receipt",
        )
        return OneBotReceipt(False, status, retcode, None, descriptor)

    descriptor = OneBotTransportDescriptor(
        layer="protocol", code="ok", retryable=False, message=""
    )
    return OneBotReceipt(True, status, retcode, message_id, descriptor)


def validate_onebot_handshake(payload: Any) -> OneBotHandshake:
    """Interpret auth challenge/accept/reject messages without I/O."""
    if not isinstance(payload, Mapping):
        descriptor = OneBotTransportDescriptor(
            layer="protocol",
            code="invalid_handshake",
            retryable=False,
            message="OneBot handshake payload was invalid",
        )
        return OneBotHandshake("invalid", False, descriptor)

    message_type = str(payload.get("type") or "").strip().lower()
    if message_type == "auth_required":
        descriptor = OneBotTransportDescriptor(
            layer="auth", code="auth_required", retryable=False, message=""
        )
        return OneBotHandshake("auth_required", False, descriptor)
    if message_type in {"auth_ok", "authenticated"}:
        descriptor = OneBotTransportDescriptor(
            layer="auth", code="authenticated", retryable=False, message=""
        )
        return OneBotHandshake("authenticated", True, descriptor)
    if message_type in {"auth_invalid", "auth_failed"}:
        descriptor = OneBotTransportDescriptor(
            layer="auth",
            code="auth_failed",
            retryable=False,
            message="OneBot WebSocket authentication was rejected",
        )
        return OneBotHandshake("rejected", False, descriptor)

    receipt = parse_onebot_receipt(payload, operation="handshake")
    if receipt.ok:
        return OneBotHandshake("accepted", True, receipt.descriptor)
    return OneBotHandshake("invalid", False, receipt.descriptor)


def validate_onebot_health(payload: Any) -> OneBotHealthStatus:
    """Validate a get_status-like response without trusting arbitrary fields."""
    receipt = parse_onebot_receipt(payload, operation="health")
    if not receipt.ok:
        return OneBotHealthStatus(False, None, None, receipt.descriptor)
    data = payload.get("data") if isinstance(payload, Mapping) else None
    online = data.get("online") if isinstance(data, Mapping) else None
    good = data.get("good") if isinstance(data, Mapping) else None
    online_value = online if isinstance(online, bool) else None
    good_value = good if isinstance(good, bool) else None
    return OneBotHealthStatus(True, online_value, good_value, receipt.descriptor)


def validate_media_url(url: Any, *, allow_file: bool = True) -> OneBotTransportDescriptor:
    """Validate media URL shape; no DNS lookup or request is performed."""
    if not isinstance(url, str) or not url.strip() or len(url.strip()) > MAX_ENDPOINT_URL_CHARS:
        return OneBotTransportDescriptor(
            layer="media", code="invalid_media_url", retryable=False,
            message="OneBot media reference is invalid",
        )
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        parsed = None
    if parsed is None:
        return OneBotTransportDescriptor(
            layer="media", code="invalid_media_url", retryable=False,
            message="OneBot media reference is invalid",
        )
    scheme = parsed.scheme.lower()
    if scheme == "file" and allow_file:
        # The adapter supports local ``file://`` URLs only.  A single-slash
        # ``file:http://...`` form is ambiguous and would otherwise fall
        # through to the HTTP downloader; UNC/remote authorities are also
        # rejected so Windows cannot be redirected to a network share.
        if (
            not url.strip().lower().startswith("file://")
            or parsed.query
            or parsed.fragment
            or not parsed.path
            or (
                parsed.netloc
                and not re.fullmatch(r"[A-Za-z]:", parsed.netloc)
            )
        ):
            return OneBotTransportDescriptor(
                layer="media", code="invalid_media_url", retryable=False,
                message="OneBot media reference is invalid",
            )
    elif scheme in {"http", "https"}:
        # Percent-encoded or backslash-containing authorities are ambiguous
        # across URL parsers and can make an apparent public host resolve to
        # a different destination in the HTTP client.
        if (
            "%" in parsed.netloc
            or "\\" in parsed.netloc
            or parsed.netloc.lower().startswith(("http:", "https:", "ws:", "wss:"))
        ):
            return OneBotTransportDescriptor(
                layer="media", code="invalid_media_url", retryable=False,
                message="OneBot media reference is invalid",
            )
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            return OneBotTransportDescriptor(
                layer="media", code="invalid_media_url", retryable=False,
                message="OneBot media reference is invalid",
            )
        try:
            parsed.port
        except ValueError:
            return OneBotTransportDescriptor(
                layer="media", code="invalid_media_url", retryable=False,
                message="OneBot media reference is invalid",
            )
        host = parsed.hostname.strip().lower().rstrip(".")
        # Reject integer/hex/octal spellings of IPv4 addresses.  They are
        # accepted by some URL stacks but make static host review unreliable.
        if (
            re.fullmatch(r"\d+", host)
            or re.fullmatch(r"0x[0-9a-f]+", host, re.IGNORECASE)
            or re.fullmatch(r"0[0-7]+", host)
        ):
            return OneBotTransportDescriptor(
                layer="media", code="invalid_media_url", retryable=False,
                message="OneBot media reference is invalid",
            )
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None:
            is_cgnat = address.version == 4 and address in ipaddress.ip_network("100.64.0.0/10")
            mapped = getattr(address, "ipv4_mapped", None)
            is_loopback = bool(address.is_loopback or (mapped and mapped.is_loopback))
            is_blocked = (
                address.is_private
                or address.is_link_local
                or address.is_reserved
                or address.is_unspecified
                or address.is_multicast
                or is_cgnat
            )
            # Local NapCat HTTP servers commonly use loopback.  Keep those
            # usable while rejecting non-loopback private/reserved literals.
            if is_blocked and not is_loopback:
                return OneBotTransportDescriptor(
                    layer="media", code="media_private_address", retryable=False,
                    message="OneBot media reference targets a private address",
                )
    else:
        return OneBotTransportDescriptor(
            layer="media", code="unsupported_media_scheme", retryable=False,
            message="OneBot media reference uses an unsupported scheme",
        )
    return OneBotTransportDescriptor(layer="media", code="media_url_ok", retryable=False)


def validate_media_response(
    status_code: Any,
    *,
    content_length: Any = None,
    body_length: Any = None,
) -> OneBotTransportDescriptor:
    """Validate HTTP media status and declared/observed body size."""
    status_descriptor = classify_http_status(status_code, operation="media_download")
    if status_descriptor.code != "http_ok":
        return replace(status_descriptor, layer="media", code="media_http_error")
    for raw_size in (content_length, body_length):
        if raw_size in (None, ""):
            continue
        try:
            size = int(raw_size)
        except (TypeError, ValueError):
            return OneBotTransportDescriptor(
                layer="media", code="invalid_media_length", retryable=False,
                message="OneBot media response length was invalid",
            )
        if size < 0 or size > MAX_MEDIA_DOWNLOAD_BYTES:
            return OneBotTransportDescriptor(
                layer="media", code="media_too_large", retryable=False,
                message="OneBot media response exceeded the size limit",
            )
    return OneBotTransportDescriptor(layer="media", code="media_ok", retryable=False)


__all__ = [
    "MAX_ENDPOINT_URL_CHARS",
    "MAX_MEDIA_DOWNLOAD_BYTES",
    "OneBotEndpoint",
    "OneBotEndpointError",
    "OneBotHealthStatus",
    "OneBotHandshake",
    "OneBotReceipt",
    "OneBotTransportDescriptor",
    "OneBotTransportError",
    "classify_http_status",
    "classify_transport_exception",
    "derive_onebot_http_url",
    "parse_onebot_endpoint",
    "parse_onebot_receipt",
    "validate_media_response",
    "validate_media_url",
    "validate_onebot_endpoints",
    "validate_onebot_handshake",
    "validate_onebot_health",
]
