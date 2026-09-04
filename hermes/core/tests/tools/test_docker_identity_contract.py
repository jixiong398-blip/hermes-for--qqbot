"""Offline Docker profile/identity and orphan-reaper contract tests."""

from datetime import datetime, timezone
from subprocess import CompletedProcess

from tools.environments import docker as docker_module


def test_label_and_container_identity_are_bounded_and_collision_resistant(monkeypatch):
    monkeypatch.setattr(docker_module, "_get_active_profile_name", lambda: "profile/a")

    assert docker_module._sanitize_label_value("profile/a") == "profile_a"
    assert docker_module._sanitize_label_value("") == "unknown"
    assert docker_module._container_identity() == "profile_a"

    left = docker_module._container_identity("shared/a")
    right = docker_module._container_identity("shared_a")
    assert left != right
    assert len(left) <= 63
    assert len(right) <= 63


def test_build_container_labels_hashes_lossy_task_ids(monkeypatch):
    monkeypatch.setattr(docker_module, "_get_active_profile_name", lambda: "main")

    labels = docker_module.build_container_labels(
        "agent:main:onebot/group/123",
        shared_container_key="trusted/team",
    )

    assert labels["hermes-agent"] == "1"
    assert labels["hermes-profile"].startswith("trusted_team-")
    assert "/" not in labels["hermes-task-id"]
    assert ":" not in labels["hermes-task-id"]
    assert len(labels["hermes-task-id"]) <= 63


def test_docker_runtime_attaches_safe_identity_labels_without_reuse(monkeypatch):
    import subprocess

    calls = []
    monkeypatch.setattr(docker_module, "find_docker", lambda: "/fake/docker")
    monkeypatch.setattr(
        docker_module.subprocess,
        "run",
        lambda cmd, **kwargs: (
            calls.append(cmd),
            subprocess.CompletedProcess(
                cmd,
                0,
                stdout="container-id\n" if len(cmd) > 1 and cmd[1] == "run" else "Docker version",
                stderr="",
            ),
        )[1],
    )
    monkeypatch.setattr(docker_module.DockerEnvironment, "init_session", lambda self: None)

    env = docker_module.DockerEnvironment(
        image="python:3.11",
        task_id="agent:main/onebot/group/123",
        persistent_filesystem=False,
    )
    run = next(cmd for cmd in calls if len(cmd) > 1 and cmd[1] == "run")
    labels = {
        run[index + 1]
        for index, flag in enumerate(run[:-1])
        if flag == "--label"
    }

    assert "hermes-agent=1" in labels
    assert any(item.startswith("hermes-task-id=") for item in labels)
    assert any(item.startswith("hermes-profile=") for item in labels)
    assert env._container_labels["hermes-agent"] == "1"


def test_orphan_reaper_only_removes_old_hermes_owned_exited_ids(monkeypatch):
    now = 2_000_000.0
    monkeypatch.setattr(docker_module.time, "time", lambda: now)
    calls = []
    stale = datetime.fromtimestamp(now - 2_000, tz=timezone.utc).isoformat()
    recent = datetime.fromtimestamp(now - 10, tz=timezone.utc).isoformat()

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1:3] == ["ps", "-a"]:
            return CompletedProcess(cmd, 0, stdout="stale-id\nrecent-id\nnot;safe\n", stderr="")
        if cmd[1] == "inspect":
            value = stale if cmd[-1] == "stale-id" else recent
            return CompletedProcess(cmd, 0, stdout=value, stderr="")
        if cmd[1] == "rm":
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(cmd)

    monkeypatch.setattr(docker_module.subprocess, "run", fake_run)
    removed = docker_module.reap_orphan_containers(
        max_age_seconds=600,
        profile_filter="profile/a",
        docker_exe="docker",
    )

    assert removed == 1
    assert any(cmd[1] == "rm" and cmd[-1] == "stale-id" for cmd in calls)
    assert not any(cmd[1] == "rm" and cmd[-1] == "recent-id" for cmd in calls)
    listing = calls[0]
    assert "label=hermes-agent=1" in listing
    assert "status=exited" in listing
    assert "label=hermes-profile=profile_a" in listing


def test_orphan_reaper_bounds_candidate_scan(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1:3] == ["ps", "-a"]:
            return CompletedProcess(cmd, 0, stdout="\n".join(f"id-{i}" for i in range(700)), stderr="")
        if cmd[1] == "inspect":
            return CompletedProcess(cmd, 0, stdout="0001-01-01T00:00:00+00:00", stderr="")
        if cmd[1] == "rm":
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(cmd)

    monkeypatch.setattr(docker_module.subprocess, "run", fake_run)
    monkeypatch.setattr(docker_module.time, "time", lambda: 2_000_000.0)

    removed = docker_module.reap_orphan_containers(docker_exe="docker")

    inspect_count = sum(1 for cmd in calls if cmd[1] == "inspect")
    assert inspect_count == docker_module._MAX_ORPHAN_SCAN
    assert removed == docker_module._MAX_ORPHAN_SCAN
