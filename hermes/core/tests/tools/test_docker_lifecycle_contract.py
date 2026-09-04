"""Offline Docker constructor lifecycle and recovery contracts."""

import subprocess
from unittest.mock import patch

from tools.environments import docker as docker_module
from tools.environments.base import BaseEnvironment


def _fake_docker_run(calls, *, run_ids):
    remaining_ids = iter(run_ids)

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        action = cmd[1] if len(cmd) > 1 else ""
        if action == "version":
            return subprocess.CompletedProcess(cmd, 0, stdout="Docker version", stderr="")
        if action == "ps":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if action == "run":
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=f"{next(remaining_ids)}\n",
                stderr="",
            )
        if action == "start":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if action == "inspect":
            fmt_index = cmd.index("--format")
            fmt = cmd[fmt_index + 1]
            if "State.Running" in fmt:
                return subprocess.CompletedProcess(cmd, 0, stdout="true\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if action == "rm":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(cmd)

    return fake_run


def test_persistent_constructor_waits_for_new_container_health(monkeypatch):
    calls = []
    monkeypatch.setattr(docker_module, "find_docker", lambda: "/fake/docker")
    monkeypatch.setattr(
        docker_module.subprocess,
        "run",
        _fake_docker_run(calls, run_ids=["fresh-id"]),
    )
    monkeypatch.setattr(
        docker_module.DockerEnvironment,
        "init_session",
        lambda self: None,
    )

    env = docker_module.DockerEnvironment(
        image="python:3.11",
        task_id="health-task",
        persistent_filesystem=True,
        persist_across_processes=True,
    )

    assert env._container_id == "fresh-id"
    assert any(
        cmd[1] == "inspect"
        and "{{.State.Running}}" in cmd
        and cmd[-1] == "fresh-id"
        for cmd in calls
    )


def test_exited_candidate_is_started_and_health_checked(monkeypatch):
    calls = []
    monkeypatch.setattr(docker_module, "find_docker", lambda: "/fake/docker")
    monkeypatch.setattr(
        docker_module,
        "find_reusable_container",
        lambda *args, **kwargs: ("stopped-id", "exited"),
    )
    monkeypatch.setattr(
        docker_module.subprocess,
        "run",
        _fake_docker_run(calls, run_ids=["unused-fresh-id"]),
    )
    monkeypatch.setattr(
        docker_module.DockerEnvironment,
        "init_session",
        lambda self: None,
    )

    env = docker_module.DockerEnvironment(
        image="python:3.11",
        task_id="restart-task",
        persistent_filesystem=True,
        persist_across_processes=True,
    )

    assert env._container_id == "stopped-id"
    assert any(cmd[1] == "start" and cmd[-1] == "stopped-id" for cmd in calls)
    assert not any(cmd[1] == "run" for cmd in calls)
    assert any(
        cmd[1] == "inspect"
        and "{{.State.Running}}" in cmd
        and cmd[-1] == "stopped-id"
        for cmd in calls
    )


def test_out_of_band_delete_recreates_and_retries_once(monkeypatch):
    calls = []
    monkeypatch.setattr(docker_module, "find_docker", lambda: "/fake/docker")
    monkeypatch.setattr(
        docker_module.subprocess,
        "run",
        _fake_docker_run(calls, run_ids=["initial-id", "recovered-id"]),
    )
    monkeypatch.setattr(
        docker_module.DockerEnvironment,
        "init_session",
        lambda self: None,
    )

    env = docker_module.DockerEnvironment(
        image="python:3.11",
        task_id="recovery-task",
        persistent_filesystem=True,
        persist_across_processes=True,
    )
    assert env._container_id == "initial-id"

    base_calls = []

    def fake_base_execute(self, command, cwd="", **kwargs):
        base_calls.append(self._container_id)
        if len(base_calls) == 1:
            return {
                "output": "docker: Error response from daemon: No such container",
                "returncode": 1,
            }
        return {"output": "recovered", "returncode": 0}

    monkeypatch.setattr(BaseEnvironment, "execute", fake_base_execute)
    result = env.execute("echo recovered")

    assert result == {"output": "recovered", "returncode": 0}
    assert base_calls == ["initial-id", "recovered-id"]
    assert env._container_id == "recovered-id"
    assert sum(1 for cmd in calls if cmd[1] == "run") == 2


def test_recovery_is_disabled_for_session_scoped_environment():
    env = object.__new__(docker_module.DockerEnvironment)
    env._persist_across_processes = True
    env._session_scoped = True
    env._container_id = "session-id"

    assert env._recreate_container() is False


def test_session_scoped_cleanup_stops_and_removes(monkeypatch):
    env = object.__new__(docker_module.DockerEnvironment)
    env._persist_across_processes = True
    env._session_scoped = True
    env._persistent = True
    env._container_id = "session-id"
    env._docker_exe = "/fake/docker"
    env._workspace_dir = None
    env._home_dir = None
    popen_calls = []

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            popen_calls.append((cmd, kwargs))

    monkeypatch.setattr(docker_module.subprocess, "Popen", FakePopen)
    env.cleanup()

    assert env._container_id is None
    assert len(popen_calls) == 2
    assert "stop" in popen_calls[0][0]
    assert "rm -f" in popen_calls[1][0]


def test_terminal_disables_cross_process_reuse_for_ephemeral_and_override_tasks(
    monkeypatch,
):
    import tools.terminal_tool as terminal_module

    captured = []

    class FakeDockerEnvironment:
        def __init__(self, **kwargs):
            captured.append(kwargs)

    monkeypatch.setattr(terminal_module, "_DockerEnvironment", FakeDockerEnvironment)
    base_config = {
        "container_persistent": False,
        "docker_persist_across_processes": True,
        "docker_extra_args": [],
        "docker_network": True,
        "docker_shared_container_key": "",
    }
    terminal_module._create_environment(
        env_type="docker",
        image="python:3.11",
        cwd="/root",
        timeout=30,
        container_config=base_config,
        task_id="ephemeral-session",
    )
    assert captured[-1]["persist_across_processes"] is False

    terminal_module.register_task_env_overrides(
        "isolated-task",
        {"docker_image": "custom:latest"},
    )
    try:
        override_config = dict(base_config, container_persistent=True)
        terminal_module._create_environment(
            env_type="docker",
            image="python:3.11",
            cwd="/root",
            timeout=30,
            container_config=override_config,
            task_id="isolated-task",
        )
    finally:
        terminal_module.clear_task_env_overrides("isolated-task")
    assert captured[-1]["persist_across_processes"] is False


def test_constructor_passes_exact_network_and_egress_identity_to_lookup(monkeypatch):
    captured = []
    monkeypatch.setattr(docker_module, "find_docker", lambda: "/fake/docker")
    monkeypatch.setattr(
        docker_module.subprocess,
        "run",
        _fake_docker_run(captured, run_ids=["fresh-id"]),
    )
    monkeypatch.setattr(
        docker_module,
        "find_reusable_container",
        lambda *args, **kwargs: (
            captured.append(("reuse", kwargs)),
            None,
        )[1],
    )
    monkeypatch.setattr(
        docker_module.DockerEnvironment,
        "init_session",
        lambda self: None,
    )

    docker_module.DockerEnvironment(
        image="python:3.11",
        task_id="airgap-task",
        persistent_filesystem=True,
        persist_across_processes=True,
        network=False,
        shared_container_key="trusted/team",
    )

    lookup = next(item[1] for item in captured if isinstance(item, tuple) and item[0] == "reuse")
    assert lookup["network_mode"] == "none"
    assert lookup["egress_label"] == "off"
    assert lookup["profile_label"].startswith("trusted_team-")
