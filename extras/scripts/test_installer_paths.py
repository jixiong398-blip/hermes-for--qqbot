"""Static packaging-path checks for the current Windows distribution layout."""

from pathlib import Path


ROOT = Path(__file__).parents[2]


def _read_text(path: Path) -> str:
    data = path.read_bytes()
    if data.startswith(b"\xff\xfe"):
        return data.decode("utf-16")
    return data.decode("utf-8-sig")


def test_inno_setup_uses_current_runtime_paths():
    text = _read_text(ROOT / "extras" / "build-installer.iss")
    assert 'Source: "extras\\scripts\\*"' in text
    assert 'Source: "modules\\napcat\\*"' in text
    assert 'Filename: "{app}\\配置API.bat"' in text
    assert 'Source: "extras\\python-installer.exe"' in text
    assert 'Source: "extras\\nodejs.zip"' in text
    assert "FixNapCat.bat" not in text
    assert "PeiZhiAPI.bat" not in text
    assert 'Source: "scripts\\*"' not in text
    assert 'Source: "napcat\\*"' not in text


def test_nsis_uses_current_runtime_paths():
    text = _read_text(ROOT / "extras" / "build-installer.nsi")
    assert 'File /r "extras\\scripts\\*.*"' in text
    assert '"modules\\napcat\\*.*"' in text
    assert 'File "配置API.bat"' in text
    assert 'hermes\\core\\requirements.txt' in text
    assert 'pip install -e "$INSTDIR\\hermes\\core"' in text
    assert 'pip install -e "$INSTDIR\\hermes"' not in text
    assert "FixNapCat.bat" not in text
    assert "PeiZhiAPI.bat" not in text
    assert 'File /r "scripts\\*.*"' not in text
    assert '"napcat\\*.*"' not in text


def test_update_batch_does_not_copy_removed_fix_helper():
    text = (ROOT / "update.bat").read_text(encoding="utf-8-sig")
    assert "FixNapCat.bat" not in text
    assert '"配置API.bat"' in text
    assert '"%SRC_DIR%\\extras\\python-installer.exe"' in text
    assert '"%SRC_DIR%\\extras\\nodejs.zip"' in text


def test_release_builder_uses_distribution_root_and_core_paths():
    text = _read_text(ROOT / "extras" / "build-release.ps1")
    assert "$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)" in text
    assert 'Test-Path "extras\\nodejs.zip"' in text
    assert 'DestinationPath "extras\\node"' in text
    assert "pip install -e hermes\\core\\ --no-deps" in text
    assert "pip install -r hermes\\core\\requirements.txt" in text
    assert "pip install -e hermes\\ --no-deps" not in text
    assert "pip install -r hermes\\requirements.txt" not in text


def test_upgrade_script_exposes_read_only_dry_run():
    text = _read_text(ROOT / "extras" / "scripts" / "upgrade.py")
    assert "dry_run" in text
    assert '"--dry-run"' in text
