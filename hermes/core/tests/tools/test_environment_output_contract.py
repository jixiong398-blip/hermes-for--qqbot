"""Offline output-capture contract tests for terminal environments."""

import json
from pathlib import Path

from tools.environments.base import BaseEnvironment, _BoundedOutputCollector


class _TestEnvironment(BaseEnvironment):
    def _run_bash(self, cmd_string, **kwargs):
        raise NotImplementedError

    def cleanup(self):
        pass


class _FinishedProcess:
    def __init__(self, chunks):
        self.stdout = iter(chunks)
        self.returncode = 0

    def poll(self):
        return 0


def test_collector_keeps_head_tail_and_spills_full_stream(tmp_path):
    spill_path = tmp_path / "terminal-output.log"
    collector = _BoundedOutputCollector(128, spill_path=spill_path)
    collector.append("HEAD-SENTINEL\n")
    collector.append("x" * 2_000)
    collector.append("\nTAIL-SENTINEL")

    rendered = collector.render()
    returned_path = collector.close_spill()

    assert returned_path == str(spill_path)
    assert len(rendered) <= 128
    assert rendered.startswith("HEAD-SENTINEL")
    assert rendered.endswith("TAIL-SENTINEL")
    assert "OUTPUT TRUNCATED" in rendered
    assert collector.buffered_chars <= 128
    assert collector.total_chars > 2_000
    assert spill_path.read_text(encoding="utf-8").startswith("HEAD-SENTINEL")
    assert "TAIL-SENTINEL" in spill_path.read_text(encoding="utf-8")


def test_bounded_wait_returns_metadata_without_truncating_internal_capture(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    import tools.tool_output_limits as limits

    monkeypatch.setattr(limits, "get_max_bytes", lambda: 128)
    environment = _TestEnvironment(cwd=str(tmp_path), timeout=5)
    result = environment._wait_for_process(
        _FinishedProcess(["HEAD-SENTINEL\n", "x" * 2_000, "\nTAIL-SENTINEL"]),
        timeout=1,
        bounded_capture=True,
    )

    assert result["returncode"] == 0
    assert len(result["output"]) <= 128
    assert "HEAD-SENTINEL" in result["output"]
    assert "TAIL-SENTINEL" in result["output"]
    assert result["output_total_chars"] > 2_000
    spill_path = Path(result["full_output_path"])
    assert spill_path.exists()
    assert "TAIL-SENTINEL" in spill_path.read_text(encoding="utf-8")


def test_default_wait_keeps_full_fidelity_for_internal_consumers(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    import tools.tool_output_limits as limits

    monkeypatch.setattr(limits, "get_max_bytes", lambda: 128)
    environment = _TestEnvironment(cwd=str(tmp_path), timeout=5)
    result = environment._wait_for_process(
        _FinishedProcess(["HEAD\n", "y" * 2_000, "\nTAIL"]),
        timeout=1,
    )

    assert result["returncode"] == 0
    assert "OUTPUT TRUNCATED" not in result["output"]
    assert len(result["output"]) > 2_000
    assert "full_output_path" not in result


def test_iterable_stdout_flushes_split_utf8_tail(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    environment = _TestEnvironment(cwd=str(tmp_path), timeout=5)
    encoded = "前后".encode("utf-8")
    result = environment._wait_for_process(
        _FinishedProcess([encoded[:2], encoded[2:5], encoded[5:]]),
        timeout=1,
    )

    assert result["returncode"] == 0
    assert result["output"] == "前后"


def test_terminal_opts_actual_base_environment_into_bounded_capture(monkeypatch, tmp_path):
    import tools.terminal_tool as terminal_module

    calls = []

    class _CaptureEnvironment(_TestEnvironment):
        env = {}

        def execute(self, command, **kwargs):
            calls.append((command, kwargs))
            return {"output": "ok", "returncode": 0}

    environment = _CaptureEnvironment(cwd=str(tmp_path), timeout=30)
    config = {
        "env_type": "local",
        "cwd": str(tmp_path),
        "timeout": 30,
        "host_cwd": None,
        "modal_mode": "auto",
        "docker_image": "",
        "singularity_image": "",
        "modal_image": "",
        "daytona_image": "",
    }
    monkeypatch.setattr(terminal_module, "_get_env_config", lambda: config)
    monkeypatch.setattr(terminal_module, "_start_cleanup_thread", lambda: None)
    monkeypatch.setattr(
        terminal_module,
        "_check_all_guards",
        lambda *_args, **_kwargs: {"approved": True},
    )
    monkeypatch.setattr(terminal_module, "_active_environments", {"default": environment})
    monkeypatch.setattr(terminal_module, "_last_activity", {"default": 0.0})
    monkeypatch.setattr(terminal_module, "_task_env_overrides", {})

    result = json.loads(terminal_module.terminal_tool("printf hello"))

    assert result["exit_code"] == 0
    assert calls == [
        (
            "printf hello",
            {"timeout": 30, "cwd": str(tmp_path), "bounded_capture": True},
        )
    ]


def test_terminal_redacts_spill_before_returning_file_handle(monkeypatch, tmp_path):
    import tools.terminal_tool as terminal_module

    hermes_home = tmp_path / ".hermes"
    spill_path = hermes_home / "cache" / "terminal-output" / "raw-output.log"
    spill_path.parent.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    secret = "OPENAI_API_KEY=sk-test-secret-value-123456"
    spill_path.write_text(f"visible\n{secret}\n", encoding="utf-8")

    class _SpillEnvironment(_TestEnvironment):
        env = {}

        def execute(self, command, **kwargs):
            return {
                "output": "visible\n... [OUTPUT TRUNCATED] ...",
                "returncode": 0,
                "output_total_chars": 60_000,
                "full_output_path": str(spill_path),
            }

    environment = _SpillEnvironment(cwd=str(tmp_path), timeout=30)
    config = {
        "env_type": "local",
        "cwd": str(tmp_path),
        "timeout": 30,
        "host_cwd": None,
        "modal_mode": "auto",
        "docker_image": "",
        "singularity_image": "",
        "modal_image": "",
        "daytona_image": "",
    }
    monkeypatch.setattr(terminal_module, "_get_env_config", lambda: config)
    monkeypatch.setattr(terminal_module, "_start_cleanup_thread", lambda: None)
    monkeypatch.setattr(
        terminal_module,
        "_check_all_guards",
        lambda *_args, **_kwargs: {"approved": True},
    )
    monkeypatch.setattr(terminal_module, "_active_environments", {"default": environment})
    monkeypatch.setattr(terminal_module, "_last_activity", {"default": 0.0})
    monkeypatch.setattr(terminal_module, "_task_env_overrides", {})

    result = json.loads(terminal_module.terminal_tool("printf output"))

    assert result["output_total_chars"] == 60_000
    assert result["full_output_path"] == str(spill_path)
    assert secret not in spill_path.read_text(encoding="utf-8")


def test_terminal_drops_backend_spill_path_outside_owned_directory(monkeypatch, tmp_path):
    import tools.terminal_tool as terminal_module

    outside_path = tmp_path / "outside.log"
    outside_path.write_text("must remain untouched", encoding="utf-8")

    class _UntrustedSpillEnvironment(_TestEnvironment):
        env = {}

        def execute(self, command, **kwargs):
            return {
                "output": "visible",
                "returncode": 0,
                "output_total_chars": 60_000,
                "full_output_path": str(outside_path),
            }

    environment = _UntrustedSpillEnvironment(cwd=str(tmp_path), timeout=30)
    config = {
        "env_type": "local",
        "cwd": str(tmp_path),
        "timeout": 30,
        "host_cwd": None,
        "modal_mode": "auto",
        "docker_image": "",
        "singularity_image": "",
        "modal_image": "",
        "daytona_image": "",
    }
    monkeypatch.setattr(terminal_module, "_get_env_config", lambda: config)
    monkeypatch.setattr(terminal_module, "_start_cleanup_thread", lambda: None)
    monkeypatch.setattr(
        terminal_module,
        "_check_all_guards",
        lambda *_args, **_kwargs: {"approved": True},
    )
    monkeypatch.setattr(terminal_module, "_active_environments", {"default": environment})
    monkeypatch.setattr(terminal_module, "_last_activity", {"default": 0.0})
    monkeypatch.setattr(terminal_module, "_task_env_overrides", {})

    result = json.loads(terminal_module.terminal_tool("printf output"))

    assert result["output"] == "visible"
    assert "full_output_path" not in result
    assert outside_path.read_text(encoding="utf-8") == "must remain untouched"


def test_collector_refuses_symlink_spill_directory_without_touching_target(
    tmp_path,
):
    real_dir = tmp_path / "real-output"
    real_dir.mkdir()
    target = real_dir / "target.txt"
    target.write_text("unchanged", encoding="utf-8")
    link_dir = tmp_path / "terminal-output"
    try:
        link_dir.symlink_to(real_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        return

    collector = _BoundedOutputCollector(32, spill_path=link_dir / "out.log")
    collector.append("x" * 100)
    assert collector.close_spill() is None
    assert target.read_text(encoding="utf-8") == "unchanged"
