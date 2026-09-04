"""OneBot plugin package with lazy adapter loading."""


def register(ctx):
    """Load the heavy adapter only when the plugin registry activates it."""

    from .adapter import register as _register

    return _register(ctx)

__all__ = ["register"]
