"""Environment connectivity failures have a distinct terminal result shape."""

import json

from tools.environments.base import EnvironmentConnectionError


def test_connection_error_carries_retryable_reason_and_hint():
    error = EnvironmentConnectionError("Docker daemon is unavailable", retry_hint="Start Docker")

    assert isinstance(error, RuntimeError)
    assert error.reason == "Docker daemon is unavailable"
    assert error.retry_hint == "Start Docker"


def test_terminal_tool_surfaces_backend_failure_as_degraded(monkeypatch):
    import tools.terminal_tool as terminal_module

    monkeypatch.setenv("TERMINAL_ENV", "ssh")
    monkeypatch.setenv("TERMINAL_SSH_HOST", "host.invalid")
    monkeypatch.setenv("TERMINAL_SSH_USER", "user")
    monkeypatch.setattr(
        terminal_module,
        "_create_environment",
        lambda **kwargs: (_ for _ in ()).throw(
            EnvironmentConnectionError("SSH backend unavailable", retry_hint="Retry later")
        ),
    )

    result = json.loads(
        terminal_module.terminal_tool(
            "echo should-not-run",
            force=True,
            task_id="connection-test",
        )
    )

    assert result["status"] == "degraded"
    assert result["retryable"] is True
    assert result["error"] == "SSH backend unavailable"
    assert result["retry_hint"] == "Retry later"
    assert result["output"] == ""
