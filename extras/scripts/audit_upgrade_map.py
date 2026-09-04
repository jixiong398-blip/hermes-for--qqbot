#!/usr/bin/env python3
"""Audit local runtime imports against the explicit upgrade map.

The normal upgrade path remains an explicit, reviewable allowlist.  This tool
only reports local Python imports that are reachable from the selected core
tree but are not present in that allowlist; it never copies files, loads a
provider, opens a database, or changes user configuration.
"""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional


MAX_RUNTIME_FILES = 10_000
MAX_SOURCE_BYTES = 5 * 1024 * 1024
MAX_IMPORTERS_PER_TARGET = 16


@dataclass(frozen=True)
class MissingImport:
    target: str
    importers: tuple[str, ...]


@dataclass(frozen=True)
class AuditReport:
    source_root: str
    runtime_files: int
    mapped_entries: int
    dynamic_python_files: int
    local_edges: int
    missing: tuple[MissingImport, ...]
    effective_missing: tuple[MissingImport, ...]
    skipped: tuple[str, ...]

    @property
    def missing_count(self) -> int:
        return len(self.missing)

    @property
    def explicit_missing_count(self) -> int:
        return len(self.missing)

    @property
    def effective_missing_count(self) -> int:
        return len(self.effective_missing)

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["missing"] = [asdict(item) for item in self.missing]
        value["missing_count"] = self.missing_count
        value["explicit_missing_count"] = self.explicit_missing_count
        value["effective_missing"] = [asdict(item) for item in self.effective_missing]
        value["effective_missing_count"] = self.effective_missing_count
        return value


def _find_bot_root(start: Path) -> Path:
    candidate = start.resolve()
    for path in (candidate, *candidate.parents):
        if (path / "install.bat").is_file() and (path / "hermes" / "core").is_dir():
            return path
    raise ValueError("could not locate bot-template root")


def _map_sources(upgrade_script: Path) -> set[str]:
    tree = ast.parse(upgrade_script.read_text(encoding="utf-8-sig"), filename=str(upgrade_script))
    mapped: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError, SyntaxError):
            continue
        if not isinstance(value, list):
            continue
        for item in value:
            if (
                isinstance(item, tuple)
                and len(item) == 2
                and isinstance(item[0], str)
            ):
                mapped.add(item[0].replace("\\", "/"))
    return mapped


def _module_path(core: Path, module_name: str) -> Optional[str]:
    if not module_name or "\x00" in module_name:
        return None
    relative = module_name.replace(".", "/")
    module_file = core / f"{relative}.py"
    if module_file.is_file():
        return f"hermes/core/{relative}.py"
    package_init = core / relative / "__init__.py"
    if package_init.is_file():
        return f"hermes/core/{relative}/__init__.py"
    return None


def _resolve_from(current: Path, core: Path, level: int, module: str | None) -> str:
    relative = current.relative_to(core).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        package = parts[:-1]
    else:
        package = parts[:-1]
    if level <= 0:
        return module or ""
    base_length = len(package) - level + 1
    if base_length < 0:
        return ""
    resolved = package[:base_length]
    if module:
        resolved.extend(module.split("."))
    return ".".join(resolved)


def _local_imports(path: Path, core: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            names.append(_resolve_from(path, core, node.level, node.module))
    return tuple(names)


def _runtime_files(core: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for path in core.rglob("*.py"):
        relative = path.relative_to(core).as_posix()
        parts = Path(relative).parts
        if (
            "/tests/" in f"/{relative}"
            or relative.startswith("tests/")
            or "/__pycache__/" in f"/{relative}"
            or relative.startswith("__pycache__/")
            or any(part.startswith(".") for part in parts)
            or path.is_symlink()
        ):
            continue
        files.append(path)
        if len(files) >= MAX_RUNTIME_FILES:
            break
    return tuple(sorted(files))


def audit(source_root: str | Path | None = None) -> AuditReport:
    bot_root = _find_bot_root(Path(source_root or Path.cwd()))
    core = bot_root / "hermes" / "core"
    upgrade_script = bot_root / "extras" / "scripts" / "upgrade.py"
    mapped = _map_sources(upgrade_script)
    files = _runtime_files(core)
    skipped: list[str] = []
    edges: dict[str, set[str]] = {}
    local_edges = 0

    for path in files:
        relative = f"core/{path.relative_to(core).as_posix()}"
        try:
            if path.stat().st_size > MAX_SOURCE_BYTES:
                skipped.append(f"{relative}:source_too_large")
                continue
            names = _local_imports(path, core)
        except (OSError, SyntaxError, UnicodeError) as error:
            skipped.append(f"{relative}:{type(error).__name__}")
            continue
        for name in names:
            if not name:
                continue
            target = _module_path(core, name)
            if target is None:
                continue
            local_edges += 1
            if target not in mapped:
                edges.setdefault(target, set()).add(relative)

    missing = tuple(
        MissingImport(target, tuple(sorted(importers)[:MAX_IMPORTERS_PER_TARGET]))
        for target, importers in sorted(edges.items())
    )
    dynamic_targets = {
        f"hermes/core/{path.relative_to(core).as_posix()}"
        for path in files
    }
    effective_missing = tuple(
        item for item in missing if item.target not in dynamic_targets
    )
    return AuditReport(
        source_root="hermes/core",
        runtime_files=len(files),
        mapped_entries=len(mapped),
        dynamic_python_files=len(dynamic_targets),
        local_edges=local_edges,
        missing=missing,
        effective_missing=effective_missing,
        skipped=tuple(sorted(skipped)),
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        help="bot-template root (defaults to the current directory or its parents)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="return exit code 2 when local imports are not covered by the effective copy policy",
    )
    args = parser.parse_args(argv)
    try:
        report = audit(args.source_root)
    except (OSError, ValueError, SyntaxError) as error:
        print(json.dumps({"error": str(error)[:240]}, ensure_ascii=False))
        return 2
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    return 2 if args.strict and report.effective_missing_count else 0


if __name__ == "__main__":  # pragma: no cover - CLI wrapper
    raise SystemExit(main())
