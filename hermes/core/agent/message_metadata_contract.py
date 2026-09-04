"""Pure message metadata helpers from upstream ``message_metadata``.

The local runtime still owns transcript persistence and provider serialization.
This port only stamps an in-memory message and appends it to an in-memory list;
it never opens SessionDB, changes a provider payload, or writes to disk.
"""

from __future__ import annotations

try:
    import hermes_bootstrap  # noqa: F401
except ModuleNotFoundError:
    hermes_bootstrap = None  # type: ignore[assignment]

from time import time as wall_time
from typing import Any, MutableMapping, Optional, TypeVar


# Durable metadata must not be counted as provider-visible context content.
PERSISTENCE_ONLY_MESSAGE_FIELDS = frozenset({"timestamp"})

_Message = TypeVar("_Message", bound=MutableMapping[str, Any])


def stamp_message_timestamp(
    message: _Message,
    *,
    timestamp: Optional[float] = None,
) -> _Message:
    """Attach a timestamp without replacing a source-provided value."""

    if message.get("timestamp") is None:
        message["timestamp"] = wall_time() if timestamp is None else timestamp
    return message


def append_message(
    messages: list[Any],
    message: _Message,
    *,
    timestamp: Optional[float] = None,
) -> _Message:
    """Stamp and append one message to an in-memory transcript list."""

    stamp_message_timestamp(message, timestamp=timestamp)
    messages.append(message)
    return message


__all__ = [
    "PERSISTENCE_ONLY_MESSAGE_FIELDS",
    "append_message",
    "stamp_message_timestamp",
]
