# Hermes Upgrade Package Matrix

> Maintainer-only planning document. This file describes what the Windows
> distribution upgrade copies, what stays opt-in, and which evidence is needed
> before widening the package. It does not authorize a production migration.

## 1. Package Layers

| Layer | Current source | Current policy | Destination | Gate |
|---|---|---|---|---|
| Runtime Python | `hermes/core/**/*.py` | bounded dynamic closure; exclude `tests/`, `docs/`, hidden/VCS directories and source links | `~/.hermes/` plus template mirror | import-graph audit, containment/link tests, py_compile |
| Active QQ plugin | `plugins/platforms/onebot/plugin.yaml` and Python module set | explicit manifest + runtime closure; OneBot is the only active QQ plugin | `~/.hermes/plugins/platforms/onebot/` | OneBot config/transport/runtime tests and manifest uniqueness |
| Templates/config | root `templates/`, selected `cli-config.yaml.example` and requirements | explicit map; preserve `config.yaml`, `SOUL.md`, `.env` | template root or `~/.hermes/` per map | setup/install dual-write and preserve-list tests |
| Installer/update definitions | `extras/build-installer.iss`, `extras/build-installer.nsi`, `extras/build-release.ps1`, `update.bat`, `upgrade.py --dry-run` | path references must match the current tree; preflight can be reviewed without writes | installer payload and existing distribution root | static path checks, dry-run/temporary smoke, ISCC/NSIS compile, clean-machine install/start |
| Optional upstream plugins | provider/platform/memory `plugin.yaml` and assets | do not auto-copy or auto-enable in the one-bot profile | future profile-scoped package | plugin discovery, capability allowlist, opt-in rollback |
| NapCat/Live2D/runtime binaries | `modules/napcat`, `modules/live2d`, portable Node/runtime packages | separate module upgrade flow; never enter Hermes core dynamic closure | distribution module roots | process/port/PID/lock and Windows packaging tests |

## 2. Runtime Python Closure

`extras/scripts/upgrade.py` keeps the explicit `UPGRADE_MAP` for reviewed
non-Python assets and selected high-risk boundaries, then appends every regular
Python file below `hermes/core` that is not already mapped. The closure is
bounded by `MAX_DYNAMIC_CORE_FILES`; source and destination paths are checked
for absolute segments, `..`, symlink/junction escapes and resolved containment.

`extras/scripts/audit_upgrade_map.py` reports both the explicit map gap and the
effective gap after the dynamic policy. A strict audit is based on the effective
gap, while the explicit gap remains visible for release review. The audit is
read-only, accepts UTF-8 BOM sources, emits relative paths only and skips an
individual malformed source instead of hiding the whole report.

## 3. Why Optional Manifests Stay Deferred

The upstream tree contains many provider, platform, memory and observability
manifests. Copying all of them into a one-bot installation would change plugin
discovery and capability visibility even when the corresponding credentials or
dependencies are absent. They must be selected by a future profile/package
manifest with:

- explicit enablement, provider and platform capability checks;
- dependency and credential redaction tests;
- no change to the local `UnifiedMemoryGateway`, OneBot session isolation or
  `SCHEMA_VERSION=11` defaults;
- per-profile rollback and a dry-run report that contains no secrets.

## 4. Release Gates

1. `UPG-DEPLOY-117/118/114/126` remain green: dynamic Python closure, path/link
   containment and AST audit.
2. `UPG-DEPLOY-115` remains the only automatic non-Python plugin manifest for
   the current QQ profile.
3. Inno/NSIS/update/release builder paths must pass `UPG-DEPLOY-126`; actual
   ISCC/NSIS compilation is a separate toolchain gate.
4. A temporary old-install upgrade must prove import closure and preserve
   user config before release.
5. A Linux copy/replay run must confirm path and permission behavior; Windows
   tests alone are insufficient.
6. Optional plugin packages require a separate Change ID and security receipt.

## 5. Rollback

Rollback of the package layer means restoring the previous code/template
snapshot or disabling the dynamic closure flag in the upgrade script. It does
not delete `~/.hermes`, databases, NapCat files, user configs, media caches,
locks or external resources.

## 6. Current Status

- Runtime Python effective import coverage: complete for the current source
  tree (`559/559` files; `effective_missing_count=0`).
- Active OneBot manifest: explicit and tested.
- Installer/update path references: statically aligned; binary compilation pending
  because ISCC/NSIS are not installed locally.
- Optional manifests, non-Python upstream assets, historical SQLite replay,
  Linux runtime and final release package: pending.
