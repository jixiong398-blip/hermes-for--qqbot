"""
Platform adapters for messaging integrations.

Each adapter handles:
- Receiving messages from a platform
- Sending messages/responses back
- Platform-specific authentication
- Message formatting and media handling
"""

from .base import BasePlatformAdapter, MessageEvent, SendResult

# YuanbaoAdapter is loaded lazily so CLI commands that do not start a gateway
# avoid importing its websocket stack. The legacy QQAdapter package was
# removed; callers should migrate QQ traffic to the active OneBot plugin.
__all__ = [
    "BasePlatformAdapter",
    "MessageEvent",
    "SendResult",
    "YuanbaoAdapter",
]


def __getattr__(name):
    if name == "YuanbaoAdapter":
        from .yuanbao import YuanbaoAdapter  # noqa: F401
        return YuanbaoAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(__all__)
