"""Pure capability contract for terminal execution environments.

Environment backends still own process creation and cleanup.  This module
provides a small, read-only description that Gateway/agent code can consume
without guessing from backend class names or controller-side platform facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class EnvironmentCapabilitySnapshot:
    """Static and lifecycle capabilities of one environment instance."""

    backend: str
    session_id: str
    cwd: str
    is_local: bool
    persistent_filesystem: bool
    supports_shell: bool
    supports_cancellation: bool
    state: str


def capability_snapshot(environment: Any) -> EnvironmentCapabilitySnapshot:
    """Describe *environment* without executing commands or touching disk."""
    if environment is None:
        return EnvironmentCapabilitySnapshot(
            backend="unknown",
            session_id="",
            cwd="",
            is_local=False,
            persistent_filesystem=False,
            supports_shell=False,
            supports_cancellation=False,
            state="unavailable",
        )

    class_name = type(environment).__name__
    backend = class_name[:-11] if class_name.endswith("Environment") else class_name
    backend = backend.strip().lower() or "unknown"
    session_id = str(getattr(environment, "_session_id", "") or "")[:240]
    cwd = str(getattr(environment, "cwd", "") or "")[:1024]
    is_local = bool(getattr(environment, "is_local", backend == "local"))
    persistent = bool(
        getattr(
            environment,
            "persistent_filesystem",
            getattr(environment, "_persistent", False),
        )
    )
    supports_shell = callable(getattr(environment, "_run_bash", None))
    supports_cancellation = callable(getattr(environment, "cleanup", None))
    if getattr(environment, "_closed", False):
        state = "closed"
    elif getattr(environment, "_snapshot_ready", False):
        state = "ready"
    else:
        state = "created"
    return EnvironmentCapabilitySnapshot(
        backend=backend,
        session_id=session_id,
        cwd=cwd,
        is_local=is_local,
        persistent_filesystem=persistent,
        supports_shell=supports_shell,
        supports_cancellation=supports_cancellation,
        state=state,
    )
