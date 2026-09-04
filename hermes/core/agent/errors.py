"""Small exception types shared by agent and gateway boundaries.

These exceptions carry no policy by themselves.  They give callers a stable
type for common startup and transport failures without coupling the gateway
to a provider SDK.
"""


class SSLConfigurationError(Exception):
    """Raised when SSL/TLS certificate bundle configuration fails."""


class EmptyStreamError(RuntimeError):
    """Raised when a provider closes a stream without yielding a response."""


class MoAPresetNotFoundError(ValueError):
    """Raised when a persisted Mixture-of-Agents preset no longer exists."""
