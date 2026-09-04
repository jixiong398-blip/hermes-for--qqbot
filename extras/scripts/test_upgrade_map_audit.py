"""Tests for the read-only upgrade-map import audit."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_audit_module():
    path = Path(__file__).with_name("audit_upgrade_map.py")
    spec = importlib.util.spec_from_file_location("audit_upgrade_map_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_audit_reports_missing_local_import_without_absolute_paths(tmp_path):
    module = _load_audit_module()
    root = tmp_path / "template"
    core = root / "hermes" / "core"
    scripts = root / "extras" / "scripts"
    (core / "agent").mkdir(parents=True)
    scripts.mkdir(parents=True)
    (root / "install.bat").write_text("", encoding="utf-8")
    (core / "agent" / "__init__.py").write_text("", encoding="utf-8")
    (core / "agent" / "caller.py").write_text(
        "from agent.missing_port import value\n", encoding="utf-8"
    )
    (core / "agent" / "missing_port.py").write_text("value = 1\n", encoding="utf-8")
    (scripts / "upgrade.py").write_text(
        "UPGRADE_MAP = [('hermes/core/agent/caller.py', 'hermes/core/agent/caller.py')]\n",
        encoding="utf-8",
    )

    report = module.audit(root)

    assert report.missing_count == 1
    assert report.explicit_missing_count == 1
    assert report.effective_missing_count == 0
    assert report.missing[0].target == "hermes/core/agent/missing_port.py"
    assert report.missing[0].importers == ("core/agent/caller.py",)
    assert str(root) not in repr(report.to_dict())


def test_audit_strict_cli_fails_only_for_missing_edges(tmp_path, monkeypatch):
    module = _load_audit_module()
    root = tmp_path / "template"
    core = root / "hermes" / "core"
    scripts = root / "extras" / "scripts"
    core.mkdir(parents=True)
    scripts.mkdir(parents=True)
    (root / "install.bat").write_text("", encoding="utf-8")
    (core / "main.py").write_text("import json\n", encoding="utf-8")
    (scripts / "upgrade.py").write_text("UPGRADE_MAP = []\n", encoding="utf-8")

    monkeypatch.chdir(root)
    assert module.main(["--strict"]) == 0


def test_audit_accepts_utf8_bom_runtime_sources(tmp_path):
    module = _load_audit_module()
    root = tmp_path / "template"
    core = root / "hermes" / "core"
    scripts = root / "extras" / "scripts"
    core.mkdir(parents=True)
    scripts.mkdir(parents=True)
    (root / "install.bat").write_text("", encoding="utf-8")
    (core / "main.py").write_bytes("\ufeffimport json\n".encode("utf-8"))
    (scripts / "upgrade.py").write_text(
        "UPGRADE_MAP = [('hermes/core/main.py', 'hermes/core/main.py')]\n",
        encoding="utf-8",
    )

    report = module.audit(root)

    assert report.skipped == ()
    assert report.missing_count == 0
