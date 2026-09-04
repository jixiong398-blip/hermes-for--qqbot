"""Offline tests for profile-scoped environment snapshot exclusion."""

import sys
import types

from tools import env_passthrough
from tools.environments.base import (
    BaseEnvironment,
    _SNAPSHOT_EXCLUDED_ENV_REGEX,
    _export_dump_excluding_session_vars,
)


class _TestableEnvironment(BaseEnvironment):
    def __init__(self, cwd="/tmp", timeout=10):
        super().__init__(cwd=cwd, timeout=timeout)

    def _run_bash(self, cmd_string, *, login=False, timeout=120, stdin_data=None):
        raise AssertionError("shell execution is not part of this offline test")

    def cleanup(self):
        pass


def _install_profile_scope(monkeypatch, *, active=True, names=()):
    scope = types.ModuleType("agent.secret_scope")
    scope.is_multiplex_active = lambda: active
    monkeypatch.setitem(sys.modules, "agent.secret_scope", scope)
    monkeypatch.setattr(env_passthrough, "get_all_passthrough", lambda: frozenset(names))


def test_export_dump_helper_excludes_session_names_without_text_filtering():
    snippet = _export_dump_excluding_session_vars(
        '"$__hermes_snap_tmp"',
        ("THIRD_PARTY_TOKEN", "bad-name", "$(touch pwned)"),
    )

    assert "${!HERMES_SESSION_*}" in snippet
    assert "HERMES_UI_SESSION_ID" in snippet
    assert "HERMES_CRON_SESSION" in snippet
    assert "THIRD_PARTY_TOKEN" in snippet
    assert "bad-name" not in snippet
    assert "touch pwned" not in snippet
    assert "export -p;" in snippet
    assert snippet.endswith('> "$__hermes_snap_tmp"')


def test_profile_exclusions_are_gated_and_monotonic(monkeypatch):
    _install_profile_scope(
        monkeypatch,
        names=("THIRD_PARTY_TOKEN", "PROFILE_CONFIG", "bad-name"),
    )
    env = _TestableEnvironment()
    env._profile_scoped_passthrough = True

    assert env._snapshot_excluded_passthrough_names() == (
        "PROFILE_CONFIG",
        "THIRD_PARTY_TOKEN",
    )

    # Once a name was captured, clearing the allowlist must not make an old
    # value eligible for a later profile through the shared snapshot.
    monkeypatch.setattr(env_passthrough, "get_all_passthrough", lambda: frozenset())
    assert env._snapshot_excluded_passthrough_names() == (
        "PROFILE_CONFIG",
        "THIRD_PARTY_TOKEN",
    )

    monkeypatch.setitem(sys.modules, "agent.secret_scope", None)
    assert env._snapshot_excluded_passthrough_names() == (
        "PROFILE_CONFIG",
        "THIRD_PARTY_TOKEN",
    )


def test_profile_scope_disabled_keeps_base_behavior(monkeypatch):
    _install_profile_scope(monkeypatch, names=("THIRD_PARTY_TOKEN",))
    env = _TestableEnvironment()

    assert env._snapshot_excluded_passthrough_names() == ()
    assert BaseEnvironment._profile_scoped_passthrough is False


def test_local_and_docker_enable_profile_exclusions_only():
    from tools.environments.docker import DockerEnvironment
    from tools.environments.local import LocalEnvironment
    from tools.environments.singularity import SingularityEnvironment

    assert LocalEnvironment._profile_scoped_passthrough is True
    assert DockerEnvironment._profile_scoped_passthrough is True
    assert SingularityEnvironment._profile_scoped_passthrough is False


def test_docker_forward_names_join_profile_exclusions(monkeypatch):
    _install_profile_scope(monkeypatch, names=("PROFILE_TOKEN",))
    from tools.environments.docker import DockerEnvironment

    env = object.__new__(DockerEnvironment)
    env._forward_env = ["DOCKER_FORWARD_TOKEN"]

    assert env._snapshot_excluded_passthrough_names() == (
        "DOCKER_FORWARD_TOKEN",
        "PROFILE_TOKEN",
    )


def test_wrap_command_restores_profile_values_after_snapshot_source(monkeypatch):
    _install_profile_scope(monkeypatch, names=("THIRD_PARTY_TOKEN",))
    env = _TestableEnvironment()
    env._profile_scoped_passthrough = True
    env._snapshot_ready = True

    wrapped = env._wrap_command("printf ok", "/tmp")

    assert "source" in wrapped
    assert "_HERMES_RUNTIME_PASSTHROUGH_THIRD_PARTY_TOKEN_PRESENT" in wrapped
    assert 'THIRD_PARTY_TOKEN="$_HERMES_RUNTIME_PASSTHROUGH_THIRD_PARTY_TOKEN_VALUE"' in wrapped
    assert "unset ${!HERMES_SESSION_*}" in wrapped
    # Keep the fork's Windows-sensitive direct export path intact.
    assert "export -p >" in wrapped


def test_init_session_unsets_profile_names_before_export(monkeypatch):
    _install_profile_scope(monkeypatch, names=("THIRD_PARTY_TOKEN",))
    env = _TestableEnvironment()
    env._profile_scoped_passthrough = True
    calls = []

    def capture(cmd_string, *, login=False, timeout=120, stdin_data=None):
        calls.append(cmd_string)
        return object()

    env._run_bash = capture
    env._wait_for_process = lambda proc, timeout=120: {
        "returncode": 0,
        "output": "",
    }
    env.init_session()

    assert env._snapshot_ready is True
    assert len(calls) == 1
    assert "unset ${!HERMES_SESSION_*}" in calls[0]
    assert "THIRD_PARTY_TOKEN" in calls[0]
    assert "export -p >" in calls[0]


def test_snapshot_regex_documents_all_session_prefixes():
    assert "HERMES_SESSION_" in _SNAPSHOT_EXCLUDED_ENV_REGEX
    assert "HERMES_CRON_AUTO_DELIVER_" in _SNAPSHOT_EXCLUDED_ENV_REGEX
    assert "HERMES_CRON_SESSION" in _SNAPSHOT_EXCLUDED_ENV_REGEX
    assert "HERMES_BROWSER_CONTROL_" in _SNAPSHOT_EXCLUDED_ENV_REGEX
