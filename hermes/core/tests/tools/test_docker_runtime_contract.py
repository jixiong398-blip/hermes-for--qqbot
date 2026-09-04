"""Offline Docker runtime reuse, network, and egress contract tests."""

from subprocess import CompletedProcess

from tools.environments import docker as docker_module


def test_egress_fingerprint_is_stable_and_does_not_expose_values():
    first = docker_module._egress_reuse_fingerprint(
        volume_args=["/host/ca.crt:/etc/ssl/certs/egress.crt:ro"],
        env_overrides={"HTTPS_PROXY": "http://proxy.invalid:9090"},
        host_args=["--add-host", "host.docker.internal:host-gateway"],
    )
    second = docker_module._egress_reuse_fingerprint(
        host_args=["--add-host", "host.docker.internal:host-gateway"],
        env_overrides={"HTTPS_PROXY": "http://proxy.invalid:9090"},
        volume_args=["/host/ca.crt:/etc/ssl/certs/egress.crt:ro"],
    )

    assert first == second
    assert first != "off"
    assert len(first) == 24
    assert "proxy.invalid" not in first
    assert docker_module._egress_reuse_fingerprint() == "off"

    labels = docker_module.build_container_labels(
        "task/a",
        egress_label=first,
    )
    assert labels["hermes-egress"] == first


def test_egress_guard_is_default_off_and_does_not_block():
    guard = docker_module.build_egress_guard(
        volume_args=["ca.crt:/etc/ssl/certs/egress.crt:ro"],
        env_overrides={"HTTPS_PROXY": "http://proxy.invalid:9090"},
        extra_args=["--network=host", "--env", "HTTPS_PROXY=direct"],
    )

    assert guard == {
        "enabled": False,
        "enforced": False,
        "fingerprint": "off",
        "critical_env_names": (),
        "extra_arg_collisions": (),
        "blocked": False,
    }


def test_egress_guard_blocks_protected_extra_args_when_enforced():
    guard = docker_module.build_egress_guard(
        enabled=True,
        enforce=True,
        env_overrides={
            "HTTPS_PROXY": "http://proxy.invalid:9090",
            "OPENAI_API_KEY": "opaque-proxy-token",
        },
        extra_args=[
            "--env",
            "HTTPS_PROXY=http://direct.invalid",
            "--env-file",
            "untrusted.env",
            "--network=host",
            "--env",
            "SAFE_SETTING=1",
        ],
    )

    assert guard["enabled"] is True
    assert guard["enforced"] is True
    assert guard["blocked"] is True
    assert set(guard["extra_arg_collisions"]) == {
        "--env-file",
        "--network=host",
        "HTTPS_PROXY",
    }
    assert "opaque-proxy-token" not in repr(guard)


def test_network_policy_preserves_requested_mode_and_reports_overrides():
    assert docker_module.build_network_policy() == {
        "requested_mode": "bridge",
        "effective_mode": "bridge",
        "extra_args": (),
        "conflicts": (),
        "blocked": False,
    }

    lockdown = docker_module.build_network_policy(network=False)
    assert lockdown["requested_mode"] == "none"
    assert lockdown["effective_mode"] == "none"
    assert lockdown["blocked"] is False

    overridden = docker_module.build_network_policy(
        network=False,
        extra_args=["--network", "host"],
    )
    assert overridden["effective_mode"] == "host"
    assert overridden["blocked"] is True

    egress_override = docker_module.build_network_policy(
        extra_args=["--network=host"],
        egress_enforced=True,
    )
    assert egress_override["blocked"] is True
    assert "egress network override" in egress_override["conflicts"]


def test_extra_args_collision_scanner_handles_split_and_equals_forms():
    critical = docker_module._critical_egress_env_names(
        {"CUSTOM_API_KEY": "opaque"}
    )

    collisions = docker_module._extra_args_egress_collisions(
        [
            "--env",
            "HTTPS_PROXY=direct",
            "--env=NODE_OPTIONS=--use-bundled-ca",
            "-eCUSTOM_API_KEY=real",
            "--env-file=secrets.env",
            "--net",
            "host",
            "--env",
            "SAFE=ok",
        ],
        critical,
    )

    assert set(collisions) == {
        "--env-file",
        "--net",
        "CUSTOM_API_KEY",
        "HTTPS_PROXY",
        "NODE_OPTIONS",
    }


def test_find_reusable_container_is_label_scoped_and_network_checked(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1] == "ps":
            return CompletedProcess(
                cmd,
                0,
                stdout=(
                    "not;safe\texited\toff\n"
                    "stopped-id\texited\toff\n"
                    "running-id\trunning\toff\n"
                ),
                stderr="",
            )
        if cmd[1] == "inspect":
            return CompletedProcess(cmd, 0, stdout="none\n", stderr="")
        raise AssertionError(cmd)

    monkeypatch.setattr(docker_module.subprocess, "run", fake_run)
    candidate = docker_module.find_reusable_container(
        "/fake/docker",
        task_label="raw/task",
        profile_label="profile-a",
        network_mode="none",
    )

    assert candidate == ("running-id", "running")
    listing = calls[0]
    assert listing[0:3] == ["/fake/docker", "ps", "-a"]
    assert "label=hermes-agent=1" in listing
    assert "label=hermes-profile=profile-a" in listing
    assert any(
        item.startswith("label=hermes-task-id=raw_task-")
        for item in listing
    )
    assert "\t" in listing[-1]
    assert all("not;safe" not in str(cmd) for cmd in calls[1:])


def test_find_reusable_container_rejects_egress_mismatch_and_unknown_network(
    monkeypatch,
):
    def fake_run_mismatch(cmd, **kwargs):
        if cmd[1] == "ps":
            return CompletedProcess(
                cmd,
                0,
                stdout="running-id\trunning\tproxy-on\n",
                stderr="",
            )
        raise AssertionError(cmd)

    monkeypatch.setattr(docker_module.subprocess, "run", fake_run_mismatch)
    assert (
        docker_module.find_reusable_container(
            "docker",
            task_label="task",
            profile_label="profile",
            egress_label="off",
        )
        is None
    )

    calls = []

    def fake_run_unknown(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1] == "ps":
            return CompletedProcess(
                cmd,
                0,
                stdout="running-id\trunning\tproxy-on\n",
                stderr="",
            )
        if cmd[1] == "inspect":
            return CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(cmd)

    monkeypatch.setattr(docker_module.subprocess, "run", fake_run_unknown)
    assert (
        docker_module.find_reusable_container(
            "docker",
            task_label="task",
            profile_label="profile",
            egress_label="proxy-on",
            network_mode="bridge",
        )
        is None
    )
    assert "label=hermes-egress=proxy-on" in calls[0]


def test_container_reuse_action_is_explicitly_opt_in():
    assert docker_module.container_reuse_action(("running-id", "running")) == "create"
    assert (
        docker_module.container_reuse_action(
            ("running-id", "running"),
            enabled=True,
        )
        == "attach"
    )
    assert (
        docker_module.container_reuse_action(
            ("stopped-id", "exited"),
            enabled=True,
        )
        == "start"
    )
    assert (
        docker_module.container_reuse_action(
            ("dead-id", "dead"),
            enabled=True,
        )
        == "create"
    )
