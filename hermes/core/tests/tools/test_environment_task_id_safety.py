"""Offline tests for persistent environment task-id path isolation."""

import hashlib

from tools.environments.base import sanitize_task_id_for_path


def test_safe_task_ids_keep_historical_directory_names():
    assert sanitize_task_id_for_path("default") == "default"
    assert sanitize_task_id_for_path("task-01.alpha") == "task-01.alpha"


def test_empty_and_path_like_ids_are_bounded_and_nontraversing():
    assert sanitize_task_id_for_path("") == "default"
    value = sanitize_task_id_for_path("agent:main:onebot:group:123/../../secret")

    assert value != "agent:main:onebot:group:123/../../secret"
    assert "/" not in value
    assert "\\" not in value
    assert len(value) <= 128
    assert value.endswith(
        hashlib.sha256(
            "agent:main:onebot:group:123/../../secret".encode("utf-8")
        ).hexdigest()[:12]
    )


def test_replacement_collisions_remain_distinct():
    left = sanitize_task_id_for_path("group/a")
    right = sanitize_task_id_for_path("group_a")

    assert left != right
    assert len(left) <= 128
    assert len(right) <= 128


def test_long_and_windows_ambiguous_ids_are_stable():
    task_id = "x" * 400 + "."

    first = sanitize_task_id_for_path(task_id)
    second = sanitize_task_id_for_path(task_id)

    assert first == second
    assert len(first) <= 128
    assert first.endswith(hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:12])
    assert not first.endswith((".", " "))


def test_windows_reserved_device_names_and_surrogates_are_safe():
    for task_id in ("CON", "con.txt", "PRN", "COM1", "LPT9"):
        value = sanitize_task_id_for_path(task_id)
        assert value != task_id
        assert len(value) <= 128
        assert "/" not in value and "\\" not in value

    malformed = "worker-\ud800"
    value = sanitize_task_id_for_path(malformed)
    assert len(value) <= 128
    assert value.endswith(
        hashlib.sha256(malformed.encode("utf-8", errors="surrogatepass"))
        .hexdigest()[:12]
    )


def test_windows_bash_lookup_skips_wsl_system32_shim(monkeypatch, tmp_path):
    import tools.environments.local as local_module

    git_bash = tmp_path / "Git" / "bin" / "bash.exe"
    git_bash.parent.mkdir(parents=True)
    git_bash.write_text("placeholder")
    monkeypatch.setattr(local_module, "_IS_WINDOWS", True)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "x86"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("SystemRoot", r"C:\\Windows")
    monkeypatch.delenv("HERMES_GIT_BASH_PATH", raising=False)
    monkeypatch.setattr(
        local_module.shutil,
        "which",
        lambda name: r"C:\\Windows\\System32\\bash.exe",
    )

    assert local_module._find_bash() == str(git_bash)


def test_docker_persistent_workspace_uses_sanitized_task_component(monkeypatch, tmp_path):
    import subprocess

    import tools.environments.base as base_module
    import tools.environments.docker as docker_module

    sandbox_root = tmp_path / "sandboxes"
    monkeypatch.setattr(base_module, "get_sandbox_dir", lambda: sandbox_root)
    monkeypatch.setattr(docker_module, "find_docker", lambda: "/fake/docker")
    monkeypatch.setattr(
        docker_module.subprocess,
        "run",
        lambda cmd, **kwargs: subprocess.CompletedProcess(
            cmd,
            0,
            stdout="fake-container-id\n" if len(cmd) > 1 and cmd[1] == "run" else "Docker version",
            stderr="",
        ),
    )
    monkeypatch.setattr(docker_module.DockerEnvironment, "init_session", lambda self: None)

    task_id = "group/a:onebot/../../secret"
    env = docker_module.DockerEnvironment(
        image="python:3.11",
        persistent_filesystem=True,
        task_id=task_id,
        volumes=[],
    )

    safe = sanitize_task_id_for_path(task_id)
    assert safe in str(env._home_dir)
    assert str(sandbox_root / "docker" / safe / "home") == env._home_dir
    assert task_id not in env._home_dir


def test_singularity_persistent_overlay_uses_sanitized_task_component(
    monkeypatch, tmp_path
):
    import tools.environments.singularity as singularity_module

    scratch = tmp_path / "scratch"
    monkeypatch.setattr(singularity_module, "_get_scratch_dir", lambda: scratch)
    monkeypatch.setattr(
        singularity_module,
        "_ensure_singularity_available",
        lambda: "apptainer",
    )
    monkeypatch.setattr(
        singularity_module,
        "_get_or_build_sif",
        lambda image, executable: image,
    )
    monkeypatch.setattr(
        singularity_module.SingularityEnvironment,
        "_start_instance",
        lambda self: setattr(self, "_instance_started", True),
    )
    monkeypatch.setattr(
        singularity_module.SingularityEnvironment,
        "init_session",
        lambda self: None,
    )

    task_id = "group/a:onebot/../../secret"
    env = singularity_module.SingularityEnvironment(
        image="docker://python:3.11",
        persistent_filesystem=True,
        task_id=task_id,
    )

    safe = sanitize_task_id_for_path(task_id)
    assert env._overlay_dir == scratch / "hermes-overlays" / f"overlay-{safe}"
    assert task_id not in str(env._overlay_dir)
