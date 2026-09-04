"""Offline SSH bulk-sync containment and error-surface contracts."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.environments import ssh as ssh_module
from tools.environments.base import EnvironmentConnectionError


def _make_env():
    env = object.__new__(ssh_module.SSHEnvironment)
    env._remote_home = "/home/testuser"
    env.host = "example.invalid"
    env.user = "testuser"
    env.port = 22
    env.key_path = ""
    env.control_socket = Path("/tmp/hermes-ssh-test.sock")
    env._build_ssh_command = lambda extra_args=None: [
        "ssh",
        "testuser@example.invalid",
    ]
    return env


def test_remote_paths_use_posix_containment_on_windows_controllers():
    env = _make_env()
    base = f"{env._remote_home}/.hermes"

    assert ssh_module._remote_relative_path(
        base,
        "/home/testuser/.hermes/skills/demo.md",
    ) == "skills/demo.md"

    with pytest.raises(ValueError, match="escapes sync base"):
        ssh_module._remote_relative_path(
            base,
            "/home/testuser/.hermes/../../outside.txt",
        )
    with pytest.raises(ValueError, match="escapes sync base"):
        ssh_module._remote_relative_path(
            base,
            "/home/testuser/.hermes-other/secret.txt",
        )
    with pytest.raises(ValueError, match="valid POSIX path"):
        ssh_module._remote_relative_path(
            base,
            r"/home/testuser/.hermes\..\secret.txt",
        )


def test_bulk_upload_rejects_escape_before_remote_mkdir_or_processes(
    tmp_path,
):
    env = _make_env()
    source = tmp_path / "payload.txt"
    source.write_text("payload", encoding="utf-8")

    with patch.object(subprocess, "run") as mock_run, \
         patch.object(subprocess, "Popen") as mock_popen:
        with pytest.raises(ValueError, match="escapes sync base"):
            env._ssh_bulk_upload(
                [(str(source), "/home/testuser/.hermes/../../outside.txt")]
            )

    mock_run.assert_not_called()
    mock_popen.assert_not_called()


def test_bulk_upload_mkdir_failure_is_retryable_connection_error(tmp_path):
    env = _make_env()
    source = tmp_path / "payload.txt"
    source.write_text("payload", encoding="utf-8")
    failed = subprocess.CompletedProcess(
        ["ssh"],
        1,
        stdout="",
        stderr="permission denied",
    )

    with patch.object(subprocess, "run", return_value=failed):
        with pytest.raises(EnvironmentConnectionError) as excinfo:
            env._ssh_bulk_upload(
                [(str(source), "/home/testuser/.hermes/skills/demo.md")]
            )

    assert "remote mkdir failed" in str(excinfo.value)
    assert "retry" in excinfo.value.retry_hint


def test_scp_failure_preserves_structured_connection_error(tmp_path):
    env = _make_env()
    source = tmp_path / "payload.txt"
    source.write_text("payload", encoding="utf-8")
    success = subprocess.CompletedProcess(["ssh"], 0, stderr="")
    failed = subprocess.CompletedProcess(
        ["scp"],
        1,
        stdout="",
        stderr=b"permission denied",
    )

    with patch.object(subprocess, "run", side_effect=[success, failed]):
        with pytest.raises(EnvironmentConnectionError) as excinfo:
            env._scp_upload(
                str(source),
                "/home/testuser/.hermes/skills/demo.md",
            )

    assert "scp failed" in str(excinfo.value)
    assert "permission denied" in str(excinfo.value)


def test_download_and_delete_failures_are_connection_errors(tmp_path):
    env = _make_env()
    destination = tmp_path / "snapshot.tar"
    failed_download = subprocess.CompletedProcess(
        ["ssh"],
        1,
        stdout="",
        stderr=b"connection reset",
    )
    with patch.object(subprocess, "run", return_value=failed_download):
        with pytest.raises(EnvironmentConnectionError, match="SSH bulk download failed"):
            env._ssh_bulk_download(destination)

    failed_delete = subprocess.CompletedProcess(
        ["ssh"],
        1,
        stdout="",
        stderr="permission denied",
    )
    with patch.object(subprocess, "run", return_value=failed_delete):
        with pytest.raises(EnvironmentConnectionError, match="remote rm failed"):
            env._ssh_delete(["/home/testuser/.hermes/cache/stale.txt"])


def test_delete_rejects_paths_outside_sync_root_before_ssh():
    env = _make_env()
    with patch.object(subprocess, "run") as mock_run:
        with pytest.raises(ValueError, match="escapes sync base"):
            env._ssh_delete(["/home/testuser/.hermes/../../outside.txt"])
    mock_run.assert_not_called()
