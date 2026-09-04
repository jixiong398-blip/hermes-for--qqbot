"""Offline capability-contract tests for terminal backends."""

from tools.environments.base import BaseEnvironment
from tools.environments.contract import EnvironmentCapabilitySnapshot, capability_snapshot


class _FakeRemoteEnvironment(BaseEnvironment):
    def _run_bash(self, cmd_string, **kwargs):
        raise AssertionError("capability probing must not execute commands")

    def cleanup(self):
        pass


def test_none_environment_is_unavailable_without_side_effects():
    snapshot = capability_snapshot(None)
    assert snapshot == EnvironmentCapabilitySnapshot(
        backend="unknown",
        session_id="",
        cwd="",
        is_local=False,
        persistent_filesystem=False,
        supports_shell=False,
        supports_cancellation=False,
        state="unavailable",
    )


def test_remote_environment_defaults_to_nonlocal_and_created_state():
    environment = _FakeRemoteEnvironment(cwd="/workspace", timeout=10)
    snapshot = environment.capability_snapshot()

    assert snapshot.backend == "_fakeremote"
    assert snapshot.is_local is False
    assert snapshot.persistent_filesystem is False
    assert snapshot.supports_shell is True
    assert snapshot.supports_cancellation is True
    assert snapshot.state == "created"


def test_local_environment_declares_controller_host(monkeypatch, tmp_path):
    from tools.environments.local import LocalEnvironment

    # Avoid LocalEnvironment.__init__ here: it intentionally bootstraps a
    # login shell, which is outside this pure capability test and uses
    # platform-specific process-session primitives on Windows.
    environment = object.__new__(LocalEnvironment)
    environment._session_id = "local-test"
    environment.cwd = str(tmp_path)
    environment._persistent = False
    environment._snapshot_ready = True
    environment._closed = False
    snapshot = environment.capability_snapshot()

    assert snapshot.backend == "local"
    assert snapshot.is_local is True
    assert snapshot.supports_shell is True
    assert snapshot.state == "ready"


def test_capability_snapshot_does_not_include_unbounded_backend_objects():
    class _Opaque:
        _session_id = "s" * 1000
        cwd = "c" * 5000
        _persistent = True
        _snapshot_ready = True

        def _run_bash(self, *args, **kwargs):
            pass

        def cleanup(self):
            pass

    snapshot = capability_snapshot(_Opaque())
    assert len(snapshot.session_id) == 240
    assert len(snapshot.cwd) == 1024
    assert snapshot.persistent_filesystem is True
    assert snapshot.state == "ready"
