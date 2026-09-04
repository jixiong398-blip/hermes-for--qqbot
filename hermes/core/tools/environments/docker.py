"""Docker execution environment for sandboxed command execution.

Security hardened (cap-drop ALL, no-new-privileges, PID limits),
configurable resource limits (CPU, memory, disk), and optional filesystem
persistence via bind mounts.
"""

import logging
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Iterable, Optional

from tools.environments.base import (
    BaseEnvironment,
    EnvironmentConnectionError,
    _popen_bash,
    sanitize_task_id_for_path,
)
from tools.environments.local import _HERMES_PROVIDER_ENV_BLOCKLIST

logger = logging.getLogger(__name__)


# Common Docker Desktop install paths checked when 'docker' is not in PATH.
# macOS Intel: /usr/local/bin, macOS Apple Silicon (Homebrew): /opt/homebrew/bin,
# Docker Desktop app bundle: /Applications/Docker.app/Contents/Resources/bin
_DOCKER_SEARCH_PATHS = [
    "/usr/local/bin/docker",
    "/opt/homebrew/bin/docker",
    "/Applications/Docker.app/Contents/Resources/bin/docker",
]

_docker_executable: Optional[str] = None  # resolved once, cached
_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LABEL_VALUE_OK_RE = re.compile(r"[^A-Za-z0-9_.-]")
_CONTAINER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MAX_ORPHAN_SCAN = 500
_EGRESS_LABEL_KEY = "hermes-egress"
_MAX_REUSE_SCAN = 64
_REUSABLE_STATES = frozenset({
    "running",
    "exited",
    "created",
})


def _normalize_forward_env_names(forward_env: list[str] | None) -> list[str]:
    """Return a deduplicated list of valid environment variable names."""
    normalized: list[str] = []
    seen: set[str] = set()

    for item in forward_env or []:
        if not isinstance(item, str):
            logger.warning("Ignoring non-string docker_forward_env entry: %r", item)
            continue

        key = item.strip()
        if not key:
            continue
        if not _ENV_VAR_NAME_RE.match(key):
            logger.warning("Ignoring invalid docker_forward_env entry: %r", item)
            continue
        if key in seen:
            continue

        seen.add(key)
        normalized.append(key)

    return normalized


def _normalize_env_dict(env: dict | None) -> dict[str, str]:
    """Validate and normalize a docker_env dict to {str: str}.

    Filters out entries with invalid variable names or non-string values.
    """
    if not env:
        return {}
    if not isinstance(env, dict):
        logger.warning("docker_env is not a dict: %r", env)
        return {}

    normalized: dict[str, str] = {}
    for key, value in env.items():
        if not isinstance(key, str) or not _ENV_VAR_NAME_RE.match(key.strip()):
            logger.warning("Ignoring invalid docker_env key: %r", key)
            continue
        key = key.strip()
        if not isinstance(value, str):
            # Coerce simple scalar types (int, bool, float) to string;
            # reject complex types.
            if isinstance(value, (int, float, bool)):
                value = str(value)
            else:
                logger.warning("Ignoring non-string docker_env value for %r: %r", key, value)
                continue
        normalized[key] = value

    return normalized


def _load_hermes_env_vars() -> dict[str, str]:
    """Load ~/.hermes/.env values without failing Docker command execution."""
    try:
        from hermes_cli.config import load_env

        return load_env() or {}
    except Exception:
        return {}


def find_docker() -> Optional[str]:
    """Locate the docker (or podman) CLI binary.

    Resolution order:
    1. ``HERMES_DOCKER_BINARY`` env var — explicit override (e.g. ``/usr/bin/podman``)
    2. ``docker`` on PATH via ``shutil.which``
    3. ``podman`` on PATH via ``shutil.which``
    4. Well-known macOS Docker Desktop install locations

    Returns the absolute path, or ``None`` if neither runtime can be found.
    """
    global _docker_executable
    if _docker_executable is not None:
        return _docker_executable

    # 1. Explicit override via env var (e.g. for Podman on immutable distros)
    override = os.getenv("HERMES_DOCKER_BINARY")
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        _docker_executable = override
        logger.info("Using HERMES_DOCKER_BINARY override: %s", override)
        return override

    # 2. docker on PATH
    found = shutil.which("docker")
    if found:
        _docker_executable = found
        return found

    # 3. podman on PATH (drop-in compatible for our use case)
    found = shutil.which("podman")
    if found:
        _docker_executable = found
        logger.info("Using podman as container runtime: %s", found)
        return found

    # 4. Well-known macOS Docker Desktop locations
    for path in _DOCKER_SEARCH_PATHS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            _docker_executable = path
            logger.info("Found docker at non-PATH location: %s", path)
            return path

    return None


def _sanitize_label_value(value: str) -> str:
    """Return a bounded Docker-label value without shell syntax."""
    if not isinstance(value, str) or not value:
        return "unknown"
    cleaned = _LABEL_VALUE_OK_RE.sub("_", value)[:63]
    return cleaned or "unknown"


def _get_active_profile_name() -> str:
    """Resolve the active Hermes profile without making profile state required."""
    try:
        from hermes_cli.profiles import get_active_profile_name

        return str(get_active_profile_name() or "default")
    except Exception:
        return "default"


def _container_identity(shared_key: str = "") -> str:
    """Return a stable profile/reuse identity suitable for Docker labels."""
    if not shared_key:
        return _sanitize_label_value(_get_active_profile_name())
    raw = str(shared_key)
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"{_sanitize_label_value(raw)[:50]}-{digest}"


def _container_task_label(task_id: str) -> str:
    """Return the same bounded collision-resistant label used at creation."""
    raw = str(task_id or "default")
    label = _sanitize_label_value(raw)
    if label != raw or len(raw) > 63:
        digest = hashlib.sha256(
            raw.encode("utf-8", errors="replace")
        ).hexdigest()[:12]
        label = f"{label[:50]}-{digest}"[:63]
    return label or "default"


def build_container_labels(
    task_id: str,
    *,
    shared_container_key: str = "",
    egress_label: str | None = None,
) -> dict[str, str]:
    """Build bounded labels for an eventual Hermes-created container.

    This pure helper does not contact Docker and is intentionally separate from
    the current single-process constructor until reuse semantics are enabled.
    """
    task_label = _container_task_label(task_id)
    labels = {
        "hermes-agent": "1",
        "hermes-task-id": task_label,
        "hermes-profile": _container_identity(shared_container_key),
    }
    if egress_label is not None:
        labels[_EGRESS_LABEL_KEY] = _sanitize_label_value(egress_label)
    return labels


def _bounded_contract_args(values: Iterable[str], *, limit: int = 128) -> list[str]:
    """Normalize untrusted Docker argv fragments for contract inspection."""
    if isinstance(values, str):
        values = (values,)
    result: list[str] = []
    try:
        iterator = iter(values or ())
    except TypeError:
        return result
    for value in iterator:
        if not isinstance(value, str):
            continue
        result.append(value[:1024])
        if len(result) >= limit:
            break
    return result


def _egress_reuse_fingerprint(
    volume_args: Iterable[str] = (),
    env_overrides: dict[str, str] | None = None,
    host_args: Iterable[str] = (),
) -> str:
    """Return a stable, non-reversible label for an egress posture.

    The raw proxy paths/tokens are never returned in the label.  ``"off"``
    represents the legacy direct-network posture; enabled proxy callers can
    use the digest to prevent reuse across different CA/token/host settings.
    """
    volumes = _bounded_contract_args(volume_args)
    hosts = _bounded_contract_args(host_args)
    env: dict[str, str] = {}
    source_env = env_overrides if isinstance(env_overrides, dict) else {}
    for key, value in source_env.items():
        if not isinstance(key, str) or not _ENV_VAR_NAME_RE.fullmatch(key):
            continue
        env[key[:128]] = str(value)[:2048]
    if not (volumes or env or hosts):
        return "off"
    payload = json.dumps(
        {"volume_args": volumes, "env_overrides": env, "host_args": hosts},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _egress_enforce_on_docker(default: bool = False) -> bool:
    """Read optional egress enforcement without enabling egress by default."""
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        proxy = cfg.get("proxy") if isinstance(cfg, dict) else {}
        if not isinstance(proxy, dict):
            proxy = {}
        return bool(proxy.get("enforce_on_docker", default))
    except Exception:
        return bool(default)


def _critical_egress_env_names(env_overrides: dict[str, str] | None = None) -> set[str]:
    """Return env names whose override can bypass a configured egress proxy."""
    critical = {
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "NO_PROXY",
        "no_proxy",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_FILE",
        "CURL_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
        "NODE_OPTIONS",
    }
    for key in (env_overrides or {}):
        if isinstance(key, str) and (
            key.endswith("_API_KEY") or key.endswith("_TOKEN")
        ):
            critical.add(key)
    return critical


def _extra_args_egress_collisions(
    extra_args: Iterable[str],
    critical_names: Iterable[str],
) -> list[str]:
    """Find ``docker run`` args that can override egress controls.

    This is an argv inspection helper only. It never executes or rewrites the
    supplied arguments, and it treats ``--env-file`` as a collision because a
    file can inject any protected variable without being inspectable here.
    """
    args = _bounded_contract_args(extra_args)
    critical = {name for name in critical_names if isinstance(name, str)}
    collisions: set[str] = set()
    env_flags = {"-e", "--env", "--env-file"}
    network_flags = {"--network", "--net"}

    i = 0
    while i < len(args):
        arg = args[i]
        next_arg = args[i + 1] if i + 1 < len(args) else ""
        if arg in env_flags:
            if arg == "--env-file":
                collisions.add(arg)
            else:
                name = next_arg.split("=", 1)[0]
                if name in critical:
                    collisions.add(name)
            i += 2
            continue
        if arg.startswith("--env-file="):
            collisions.add("--env-file")
        elif arg.startswith("--env="):
            name = arg.split("=", 1)[1].split("=", 1)[0]
            if name in critical:
                collisions.add(name)
        elif arg.startswith("-e") and len(arg) > 2:
            name = arg[2:].split("=", 1)[0]
            if name in critical:
                collisions.add(name)
        elif arg in network_flags:
            collisions.add(arg)
            i += 1
        elif any(arg.startswith(f"{flag}=") for flag in network_flags):
            collisions.add(arg)
        i += 1
    return sorted(collisions)


def _network_mode_from_extra_args(extra_args: Iterable[str]) -> str | None:
    """Extract the last explicit Docker network mode from argv fragments."""
    args = _bounded_contract_args(extra_args)
    mode: str | None = None
    for index, arg in enumerate(args):
        if arg in {"--network", "--net"}:
            if index + 1 < len(args):
                mode = args[index + 1].strip().lower() or None
        elif arg.startswith("--network="):
            mode = arg.split("=", 1)[1].strip().lower() or None
        elif arg.startswith("--net="):
            mode = arg.split("=", 1)[1].strip().lower() or None
    if mode == "default":
        return "bridge"
    return mode


def build_network_policy(
    network: bool = True,
    extra_args: Iterable[str] = (),
    *,
    egress_enforced: bool = False,
) -> dict[str, object]:
    """Describe Docker network intent without changing the legacy constructor.

    ``network=False`` means ``none`` as in the existing constructor.  An
    explicit extra-arg override is reported, but only an explicit caller that
    asks for enforcement receives ``blocked=True``.  The current constructor
    does not call this helper, so existing users retain their exact argv.
    """
    args = _bounded_contract_args(extra_args)
    requested = "bridge" if bool(network) else "none"
    explicit = _network_mode_from_extra_args(args)
    effective = explicit or requested
    conflicts: set[str] = set()
    if not network and explicit and explicit != "none":
        conflicts.add("network=false overridden")
    if egress_enforced and explicit:
        conflicts.add("egress network override")
    return {
        "requested_mode": requested,
        "effective_mode": effective,
        "extra_args": tuple(args),
        "conflicts": tuple(sorted(conflicts)),
        "blocked": bool(conflicts),
    }


def build_egress_guard(
    *,
    enabled: bool = False,
    enforce: bool | None = None,
    volume_args: Iterable[str] = (),
    env_overrides: dict[str, str] | None = None,
    host_args: Iterable[str] = (),
    extra_args: Iterable[str] = (),
) -> dict[str, object]:
    """Build a bounded egress safety decision for a future Docker caller.

    The default is a complete no-op: no collision is blocking and the
    fingerprint is ``"off"``.  When a caller explicitly enables egress, a
    configured enforcement flag can fail closed on protected env/network
    overrides; raw proxy credentials remain absent from the returned object.
    """
    active = bool(enabled)
    enforced = (
        bool(enforce)
        if enforce is not None
        else (_egress_enforce_on_docker(default=False) if active else False)
    )
    critical = _critical_egress_env_names(env_overrides) if active else set()
    collisions = (
        _extra_args_egress_collisions(extra_args, critical)
        if active
        else []
    )
    fingerprint = (
        _egress_reuse_fingerprint(volume_args, env_overrides, host_args)
        if active
        else "off"
    )
    return {
        "enabled": active,
        "enforced": bool(enforced),
        "fingerprint": fingerprint,
        "critical_env_names": tuple(sorted(critical)),
        "extra_arg_collisions": tuple(collisions),
        "blocked": bool(active and enforced and collisions),
    }


def _inspect_network_mode(docker_exe: str, container_id: str) -> str | None:
    """Read a candidate's network mode; ``None`` is an unknown/fail-closed result."""
    try:
        result = subprocess.run(
            [
                docker_exe,
                "inspect",
                "--format",
                "{{.HostConfig.NetworkMode}}",
                container_id,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    mode = (result.stdout or "").strip().lower()
    return "bridge" if mode == "default" else (mode or None)


def find_reusable_container(
    docker_exe: str,
    *,
    task_label: str,
    profile_label: str,
    egress_label: str = "off",
    network_mode: str | None = None,
    max_candidates: int = _MAX_REUSE_SCAN,
) -> tuple[str, str] | None:
    """Find a profile/task-matched container without starting or deleting it.

    The query is intentionally opt-in and read-only.  It filters for Hermes'
    ownership label, exact bounded task/profile labels, and (when enabled) an
    exact egress fingerprint.  ``egress_label='off'`` additionally rejects a
    container carrying a non-off egress label, preventing a later direct-mode
    session from inheriting proxy mounts or tokens.  A requested network mode
    is inspected separately and unknown results are rejected.
    """
    docker = str(docker_exe or "docker")[:500]
    task = _container_task_label(task_label)
    profile = _sanitize_label_value(str(profile_label or "default"))
    egress = _sanitize_label_value(str(egress_label or "off"))
    try:
        limit = max(1, min(int(max_candidates), _MAX_REUSE_SCAN))
    except (TypeError, ValueError):
        limit = _MAX_REUSE_SCAN

    filters = [
        "--filter",
        "label=hermes-agent=1",
        "--filter",
        f"label=hermes-task-id={task}",
        "--filter",
        f"label=hermes-profile={profile}",
    ]
    if egress != "off":
        filters.extend(["--filter", f"label={_EGRESS_LABEL_KEY}={egress}"])
        output_format = "{{.ID}}\t{{.State}}"
    else:
        output_format = '{{.ID}}\t{{.State}}\t{{.Label "' + _EGRESS_LABEL_KEY + '"}}'

    try:
        result = subprocess.run(
            [docker, "ps", "-a", *filters, "--format", output_format],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None

    running: tuple[str, str] | None = None
    first: tuple[str, str] | None = None
    inspected_count = 0
    for line in (result.stdout or "").splitlines():
        if inspected_count >= limit:
            break
        parts = line.split("\t", 2)
        if egress == "off":
            if len(parts) < 3:
                continue
            container_id, state, found_egress = (
                parts[0].strip(),
                parts[1].strip().lower(),
                parts[2].strip().lower(),
            )
            if found_egress not in ("", "<no value>", "off"):
                continue
        else:
            if len(parts) < 2:
                continue
            container_id, state = parts[0].strip(), parts[1].strip().lower()
        if not _CONTAINER_ID_RE.fullmatch(container_id):
            continue
        if state not in _REUSABLE_STATES:
            continue
        inspected_count += 1

        if network_mode is not None:
            expected = str(network_mode).strip().lower()
            if expected == "default":
                expected = "bridge"
            if not expected or _inspect_network_mode(docker, container_id) != expected:
                continue

        candidate = (container_id, state)
        if first is None:
            first = candidate
        if state == "running" and running is None:
            running = candidate
    return running or first


def container_reuse_action(
    candidate: tuple[str, str] | None,
    *,
    enabled: bool = False,
) -> str:
    """Return the explicit action for a reuse candidate without side effects."""
    if (
        not enabled
        or not isinstance(candidate, (tuple, list))
        or len(candidate) < 2
    ):
        return "create"
    state = str(candidate[1]).strip().lower()
    if state == "running":
        return "attach"
    if state in {"exited", "created"}:
        return "start"
    return "create"


def _parse_finished_at(value: object) -> Optional[float]:
    """Parse Docker's RFC3339 finished timestamp without raising."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def reap_orphan_containers(
    *,
    max_age_seconds: int = 600,
    profile_filter: Optional[str] = None,
    docker_exe: Optional[str] = None,
) -> int:
    """Remove only stale, exited containers owned by Hermes.

    The helper is best-effort and offline-testable: it uses argv lists, caps the
    scan at 500 ids, filters by Hermes ownership plus ``status=exited``, and
    never removes running containers or containers from another profile when a
    filter is supplied. It is not called automatically by the current runtime.
    """
    try:
        age = max(0, min(int(max_age_seconds), 7 * 24 * 60 * 60))
    except (TypeError, ValueError):
        age = 600
    docker = str(docker_exe or find_docker() or "docker")[:500]
    filters = ["--filter", "label=hermes-agent=1", "--filter", "status=exited"]
    if profile_filter:
        filters.extend([
            "--filter",
            f"label=hermes-profile={_sanitize_label_value(str(profile_filter))}",
        ])
    try:
        listing = subprocess.run(
            [docker, "ps", "-a", *filters, "--format", "{{.ID}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.debug("Docker orphan listing failed", exc_info=True)
        return 0
    if listing.returncode != 0:
        return 0

    candidates: list[str] = []
    for raw_id in (listing.stdout or "").splitlines():
        container_id = raw_id.strip()
        if not _CONTAINER_ID_RE.fullmatch(container_id) or container_id in candidates:
            continue
        candidates.append(container_id)
        if len(candidates) >= _MAX_ORPHAN_SCAN:
            break

    cutoff = time.time() - age
    removed = 0
    for container_id in candidates:
        try:
            inspected = subprocess.run(
                [docker, "inspect", "--format", "{{.State.FinishedAt}}", container_id],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
                stdin=subprocess.DEVNULL,
            )
            finished_at = _parse_finished_at(inspected.stdout)
            if inspected.returncode != 0 or finished_at is None or finished_at >= cutoff:
                continue
            result = subprocess.run(
                [docker, "rm", "-f", container_id],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
                stdin=subprocess.DEVNULL,
            )
            if result.returncode == 0:
                removed += 1
        except (OSError, subprocess.TimeoutExpired):
            logger.debug("Docker orphan cleanup failed for %s", container_id[:32], exc_info=True)
    return removed


# Security flags applied to every container.
# The container itself is the security boundary (isolated from host).
# We drop all capabilities then add back the minimum needed:
#   DAC_OVERRIDE - root can write to bind-mounted dirs owned by host user
#   CHOWN/FOWNER - package managers (pip, npm, apt) need to set file ownership
#   SETUID/SETGID - the image entrypoint drops from root to the 'hermes'
#       user via `gosu`, which requires these caps. Combined with
#       `no-new-privileges`, gosu still cannot escalate back to root after
#       the drop, so the security posture is preserved. Omitted entirely
#       when the container starts as a non-root user via --user, since
#       no gosu drop is needed in that mode.
# Block privilege escalation and limit PIDs.
# /tmp is size-limited and nosuid but allows exec (needed by pip/npm builds).
_BASE_SECURITY_ARGS = [
    "--cap-drop", "ALL",
    "--cap-add", "DAC_OVERRIDE",
    "--cap-add", "CHOWN",
    "--cap-add", "FOWNER",
    "--security-opt", "no-new-privileges",
    "--pids-limit", "256",
    "--tmpfs", "/tmp:rw,nosuid,size=512m",
    "--tmpfs", "/var/tmp:rw,noexec,nosuid,size=256m",
    "--tmpfs", "/run:rw,noexec,nosuid,size=64m",
]

# Extra caps needed when the container starts as root and an entrypoint
# must drop privileges via gosu/su. Skipped when --user is passed because
# the container already starts unprivileged and never needs to switch.
_GOSU_CAP_ARGS = [
    "--cap-add", "SETUID",
    "--cap-add", "SETGID",
]


def _build_security_args(run_as_host_user: bool) -> list[str]:
    """Return the security/cap/tmpfs args tailored to the privilege mode."""
    if run_as_host_user:
        return list(_BASE_SECURITY_ARGS)
    return list(_BASE_SECURITY_ARGS) + list(_GOSU_CAP_ARGS)


def _resolve_host_user_spec() -> Optional[str]:
    """Return ``<uid>:<gid>`` for the current host user, or ``None`` on platforms
    where this is not meaningful (e.g. Windows without posix ids).

    We intentionally read ``os.getuid()``/``os.getgid()`` directly rather than
    going through ``getpass``/``pwd`` so this stays cheap and never raises on
    nameless UIDs (nss lookups can fail inside sandboxed launchers).
    """
    get_uid = getattr(os, "getuid", None)
    get_gid = getattr(os, "getgid", None)
    if get_uid is None or get_gid is None:
        return None
    try:
        return f"{get_uid()}:{get_gid()}"
    except Exception:  # pragma: no cover - defensive
        return None


_storage_opt_ok: Optional[bool] = None  # cached result across instances


def _ensure_docker_available() -> None:
    """Best-effort check that the docker CLI is available before use.

    Reuses ``find_docker()`` so this preflight stays consistent with the rest of
    the Docker backend, including known non-PATH Docker Desktop locations.
    """
    docker_exe = find_docker()
    if not docker_exe:
        logger.error(
            "Docker backend selected but no docker executable was found in PATH "
            "or known install locations. Install Docker Desktop and ensure the "
            "CLI is available."
        )
        raise EnvironmentConnectionError(
            "Docker executable not found in PATH or known install locations. "
            "Install Docker and ensure the 'docker' command is available."
        )

    try:
        result = subprocess.run(
            [docker_exe, "version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        logger.error(
            "Docker backend selected but the resolved docker executable '%s' could "
            "not be executed.",
            docker_exe,
            exc_info=True,
        )
        raise EnvironmentConnectionError(
            "Docker executable could not be executed. Check your Docker installation."
        )
    except subprocess.TimeoutExpired:
        logger.error(
            "Docker backend selected but '%s version' timed out. "
            "The Docker daemon may not be running.",
            docker_exe,
            exc_info=True,
        )
        raise EnvironmentConnectionError(
            "Docker daemon is not responding. Ensure Docker is running and try again."
        )
    except Exception:
        logger.error(
            "Unexpected error while checking Docker availability.",
            exc_info=True,
        )
        raise
    else:
        if result.returncode != 0:
            logger.error(
                "Docker backend selected but '%s version' failed "
                "(exit code %d, stderr=%s)",
                docker_exe,
                result.returncode,
                result.stderr.strip(),
            )
            raise EnvironmentConnectionError(
                "Docker command is available but 'docker version' failed. "
                "Check your Docker installation."
            )


class DockerEnvironment(BaseEnvironment):
    """Hardened Docker container execution with resource limits and persistence.

    Security: all capabilities dropped, no privilege escalation, PID limits,
    size-limited tmpfs for scratch dirs. The container itself is the security
    boundary — the filesystem inside is writable so agents can install packages
    (pip, npm, apt) as needed. Writable workspace via tmpfs or bind mounts.

    Persistence: when enabled, bind mounts preserve /workspace and /root
    across container restarts.
    """

    _profile_scoped_passthrough = True

    def _additional_profile_scoped_passthrough_names(self) -> Iterable[str]:
        """Keep explicit docker_forward_env values out of shared snapshots."""
        return tuple(getattr(self, "_forward_env", ()) or ())

    def __init__(
        self,
        image: str,
        cwd: str = "/root",
        timeout: int = 60,
        cpu: float = 0,
        memory: int = 0,
        disk: int = 0,
        persistent_filesystem: bool = False,
        task_id: str = "default",
        volumes: list = None,
        forward_env: list[str] | None = None,
        env: dict | None = None,
        network: bool = True,
        host_cwd: str = None,
        auto_mount_cwd: bool = False,
        run_as_host_user: bool = False,
        extra_args: Iterable[str] | None = None,
        persist_across_processes: bool = False,
        shared_container_key: str = "",
    ):
        if cwd == "~":
            cwd = "/root"
        super().__init__(cwd=cwd, timeout=timeout)
        self._persistent = bool(persistent_filesystem)
        # Filesystem persistence and process persistence are separate
        # contracts. The terminal factory disables process persistence for
        # session-scoped/override environments; direct backend callers may
        # still exercise the two knobs independently.
        self._persist_across_processes = bool(persist_across_processes)
        self._session_scoped = False
        self._shared_container_key = str(shared_container_key or "")[:500]
        self._extra_args = _bounded_contract_args(extra_args)
        self._network_requested = bool(network)
        self._network_mode = (
            _network_mode_from_extra_args(self._extra_args)
            or ("none" if not self._network_requested else "bridge")
        )
        self._task_id = task_id
        self._forward_env = _normalize_forward_env_names(forward_env)
        self._env = _normalize_env_dict(env)
        self._container_id: Optional[str] = None
        logger.info(f"DockerEnvironment volumes: {volumes}")
        # Ensure volumes is a list (config.yaml could be malformed)
        if volumes is not None and not isinstance(volumes, list):
            logger.warning(f"docker_volumes config is not a list: {volumes!r}")
            volumes = []

        # Fail fast if Docker is not available.
        _ensure_docker_available()

        # Build resource limit args
        resource_args = []
        if cpu > 0:
            resource_args.extend(["--cpus", str(cpu)])
        if memory > 0:
            resource_args.extend(["--memory", f"{memory}m"])
        if disk > 0 and sys.platform != "darwin":
            if self._storage_opt_supported():
                resource_args.extend(["--storage-opt", f"size={disk}m"])
            else:
                logger.warning(
                    "Docker storage driver does not support per-container disk limits "
                    "(requires overlay2 on XFS with pquota). Container will run without disk quota."
                )
        if not network:
            resource_args.append("--network=none")

        # Persistent workspace via bind mounts from a configurable host directory
        # (TERMINAL_SANDBOX_DIR, default ~/.hermes/sandboxes/). Non-persistent
        # mode uses tmpfs (ephemeral, fast, gone on cleanup).
        from tools.environments.base import get_sandbox_dir

        # User-configured volume mounts (from config.yaml docker_volumes)
        volume_args = []
        workspace_explicitly_mounted = False
        for vol in (volumes or []):
            if not isinstance(vol, str):
                logger.warning(f"Docker volume entry is not a string: {vol!r}")
                continue
            vol = vol.strip()
            if not vol:
                continue
            if ":" in vol:
                volume_args.extend(["-v", vol])
                if ":/workspace" in vol:
                    workspace_explicitly_mounted = True
            else:
                logger.warning(f"Docker volume '{vol}' missing colon, skipping")

        host_cwd_abs = os.path.abspath(os.path.expanduser(host_cwd)) if host_cwd else ""
        bind_host_cwd = (
            auto_mount_cwd
            and bool(host_cwd_abs)
            and os.path.isdir(host_cwd_abs)
            and not workspace_explicitly_mounted
        )
        if auto_mount_cwd and host_cwd and not os.path.isdir(host_cwd_abs):
            logger.debug(f"Skipping docker cwd mount: host_cwd is not a valid directory: {host_cwd}")

        self._workspace_dir: Optional[str] = None
        self._home_dir: Optional[str] = None
        writable_args = []
        if self._persistent:
            sandbox = get_sandbox_dir() / "docker" / sanitize_task_id_for_path(task_id)
            self._home_dir = str(sandbox / "home")
            os.makedirs(self._home_dir, exist_ok=True)
            writable_args.extend([
                "-v", f"{self._home_dir}:/root",
            ])
            if not bind_host_cwd and not workspace_explicitly_mounted:
                self._workspace_dir = str(sandbox / "workspace")
                os.makedirs(self._workspace_dir, exist_ok=True)
                writable_args.extend([
                    "-v", f"{self._workspace_dir}:/workspace",
                ])
        else:
            if not bind_host_cwd and not workspace_explicitly_mounted:
                writable_args.extend([
                    "--tmpfs", "/workspace:rw,exec,size=10g",
                ])
            writable_args.extend([
                "--tmpfs", "/home:rw,exec,size=1g",
                "--tmpfs", "/root:rw,exec,size=1g",
            ])

        if bind_host_cwd:
            logger.info(f"Mounting configured host cwd to /workspace: {host_cwd_abs}")
            volume_args = ["-v", f"{host_cwd_abs}:/workspace", *volume_args]
        elif workspace_explicitly_mounted:
            logger.debug("Skipping docker cwd mount: /workspace already mounted by user config")

        # Mount credential files (OAuth tokens, etc.) declared by skills.
        # Read-only so the container can authenticate but not modify host creds.
        try:
            from tools.credential_files import (
                get_credential_file_mounts,
                get_skills_directory_mount,
                get_cache_directory_mounts,
            )

            for mount_entry in get_credential_file_mounts():
                volume_args.extend([
                    "-v",
                    f"{mount_entry['host_path']}:{mount_entry['container_path']}:ro",
                ])
                logger.info(
                    "Docker: mounting credential %s -> %s",
                    mount_entry["host_path"],
                    mount_entry["container_path"],
                )

            # Mount skill directories (local + external) so skill
            # scripts/templates are available inside the container.
            for skills_mount in get_skills_directory_mount():
                volume_args.extend([
                    "-v",
                    f"{skills_mount['host_path']}:{skills_mount['container_path']}:ro",
                ])
                logger.info(
                    "Docker: mounting skills dir %s -> %s",
                    skills_mount["host_path"],
                    skills_mount["container_path"],
                )

            # Mount host-side cache directories (documents, images, audio,
            # screenshots) so the agent can access uploaded files and other
            # cached media from inside the container.  Read-only — the
            # container reads these but the host gateway manages writes.
            for cache_mount in get_cache_directory_mounts():
                volume_args.extend([
                    "-v",
                    f"{cache_mount['host_path']}:{cache_mount['container_path']}:ro",
                ])
                logger.info(
                    "Docker: mounting cache dir %s -> %s",
                    cache_mount["host_path"],
                    cache_mount["container_path"],
                )
        except Exception as e:
            logger.debug("Docker: could not load credential file mounts: %s", e)

        # Explicit environment variables (docker_env config) — set at container
        # creation so they're available to all processes (including entrypoint).
        env_args = []
        for key in sorted(self._env):
            env_args.extend(["-e", f"{key}={self._env[key]}"])

        # Optional: run the container as the host user so files written into
        # bind-mounted dirs (/workspace, /root, docker_volumes entries) are
        # owned by that user on the host instead of by root. Skip cleanly on
        # platforms without POSIX uid/gid (e.g. native Windows Docker).
        user_args: list[str] = []
        if run_as_host_user:
            user_spec = _resolve_host_user_spec()
            if user_spec is not None:
                user_args = ["--user", user_spec]
                logger.info("Docker: running container as host user %s", user_spec)
            else:
                logger.warning(
                    "docker_run_as_host_user is enabled but this platform does "
                    "not expose POSIX uid/gid; container will start as its "
                    "image default user."
                )
                # Fall back to the full cap set — without --user, an image's
                # entrypoint may still need gosu/su to drop privileges.
        security_args = _build_security_args(run_as_host_user and bool(user_args))

        logger.info(f"Docker volume_args: {volume_args}")
        all_run_args = (
            security_args
            + user_args
            + writable_args
            + resource_args
            + volume_args
            + env_args
            + self._extra_args
        )
        logger.info(f"Docker run_args: {all_run_args}")

        # Resolve the docker executable once so it works even when
        # /usr/local/bin is not in PATH (common on macOS gateway/service).
        self._docker_exe = find_docker() or "docker"

        # Start the container directly via `docker run -d`.
        container_name = f"hermes-{uuid.uuid4().hex[:8]}"
        # The current fork has no live egress proxy integration in this
        # constructor.  Recording the explicit ``off`` posture still prevents
        # a future direct-mode process from attaching to a proxy-tagged
        # container created by a newer process.
        container_labels = build_container_labels(
            task_id,
            shared_container_key=self._shared_container_key,
            egress_label="off",
        )
        label_args: list[str] = []
        for key, value in container_labels.items():
            label_args.extend(["--label", f"{key}={value}"])
        self._container_labels = dict(container_labels)
        self._container_name = container_name
        self._image = image
        self._label_args = list(label_args)
        self._all_run_args = list(all_run_args)
        reused = False
        if self._persist_across_processes:
            reused = self._reuse_existing_container()

        if not reused:
            self._start_new_container()
            if self._persist_across_processes and not self._wait_for_container_ready(
                self._container_id
            ):
                failed_id = (self._container_id or "")[:32]
                self._remove_container_ref(self._container_id or container_name)
                self._container_id = None
                raise EnvironmentConnectionError(
                    f"Docker container {failed_id or container_name[:32]} "
                    "did not become ready after startup."
                )

        # Build the init-time env forwarding args (used only by init_session
        # to inject host env vars into the snapshot; subsequent commands get
        # them from the snapshot file).
        self._init_env_args = self._build_init_env_args()

        # Initialize session snapshot inside the container
        self.init_session()

    def _remove_container_ref(self, container_ref: str) -> None:
        """Best-effort removal of a container name or id after failed startup."""
        if not container_ref:
            return
        try:
            subprocess.run(
                [self._docker_exe, "rm", "-f", str(container_ref)[:128]],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            logger.debug("Could not remove Docker container %s", str(container_ref)[:32])

    def _start_new_container(self) -> None:
        """Create the configured container and retain immutable recovery state."""
        run_cmd = [
            self._docker_exe,
            "run",
            "-d",
            "--init",
            "--name",
            self._container_name,
            *self._label_args,
            "-w",
            self.cwd,
            *self._all_run_args,
            self._image,
            "sleep",
            "infinity",
        ]
        logger.debug("Starting container %s", self._container_name)
        try:
            result = subprocess.run(
                run_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=True,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            # Docker can leave a named Created object when run fails during an
            # image pull/start. Remove only our known name; never sweep by
            # profile or task here.
            self._remove_container_ref(self._container_name)
            raise
        container_id = (result.stdout or "").strip()
        if not _CONTAINER_ID_RE.fullmatch(container_id):
            self._remove_container_ref(self._container_name)
            raise EnvironmentConnectionError(
                "Docker run returned an invalid or empty container id."
            )
        self._container_id = container_id
        logger.info(
            "Started container %s (%s)",
            self._container_name,
            container_id[:12],
        )

    def _wait_for_container_ready(
        self,
        container_id: str | None,
        *,
        timeout: int = 15,
    ) -> bool:
        """Wait for Docker's running state using bounded inspect calls."""
        if not container_id:
            return False
        try:
            wait_seconds = max(1, min(int(timeout), 60))
        except (TypeError, ValueError):
            wait_seconds = 15
        deadline = time.monotonic() + wait_seconds
        while True:
            try:
                result = subprocess.run(
                    [
                        self._docker_exe,
                        "inspect",
                        "--format",
                        "{{.State.Running}}",
                        str(container_id)[:128],
                    ],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                    check=False,
                    stdin=subprocess.DEVNULL,
                )
            except (OSError, subprocess.TimeoutExpired):
                return False
            if result.returncode != 0:
                return False
            if (result.stdout or "").strip().lower() == "true":
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)

    def _reuse_existing_container(self) -> bool:
        """Attach or restart one exact label/network-matched candidate."""
        candidate = find_reusable_container(
            self._docker_exe,
            task_label=self._container_labels.get("hermes-task-id", "default"),
            profile_label=self._container_labels.get("hermes-profile", "default"),
            egress_label=self._container_labels.get(_EGRESS_LABEL_KEY, "off"),
            network_mode=self._network_mode,
        )
        if candidate is None:
            return False
        candidate_id, candidate_state = candidate
        if candidate_state == "running":
            if self._wait_for_container_ready(candidate_id):
                self._container_id = candidate_id
                return True
            return False
        if candidate_state not in {"exited", "created"}:
            return False
        try:
            subprocess.run(
                [self._docker_exe, "start", candidate_id],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=True,
                stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            logger.warning(
                "Could not restart reusable Docker container %s: %s",
                candidate_id[:12],
                exc,
            )
            return False
        if not self._wait_for_container_ready(candidate_id):
            logger.warning(
                "Reusable Docker container %s did not become ready",
                candidate_id[:12],
            )
            return False
        self._container_id = candidate_id
        return True

    _NO_CONTAINER_PATTERNS = (
        "no such container",
        "is not running",
        "container not found",
    )

    def _is_container_gone(self, output: object) -> bool:
        """Return True when Docker reports that the selected container vanished."""
        text = str(output or "").lower()
        return any(pattern in text for pattern in self._NO_CONTAINER_PATTERNS)

    def _recreate_container(self) -> bool:
        """Recover a persist-mode container after an out-of-band deletion."""
        if not self._persist_across_processes or self._session_scoped:
            return False
        old_id = (self._container_id or "")[:12]
        logger.warning("Docker container %s disappeared; attempting recovery", old_id)
        self._container_id = None

        if self._reuse_existing_container():
            try:
                self._snapshot_ready = False
                self._init_env_args = self._build_init_env_args()
                self.init_session()
                return bool(self._container_id)
            except Exception:
                logger.warning("Docker recovery snapshot initialization failed", exc_info=True)
                self._container_id = None

        self._container_name = f"hermes-{uuid.uuid4().hex[:8]}"
        try:
            self._start_new_container()
            if not self._wait_for_container_ready(self._container_id):
                self._remove_container_ref(self._container_id or self._container_name)
                self._container_id = None
                return False
            self._snapshot_ready = False
            self._init_env_args = self._build_init_env_args()
            self.init_session()
            return bool(self._container_id)
        except (EnvironmentConnectionError, OSError, subprocess.SubprocessError):
            logger.warning("Docker recovery could not recreate container", exc_info=True)
            self._container_id = None
            return False

    def execute(self, command: str, cwd: str = "", **kwargs) -> dict:
        """Execute once and retry exactly once after a missing container."""
        result = super().execute(command, cwd, **kwargs)
        if (
            result.get("returncode", 0) != 0
            and self._is_container_gone(result.get("output", ""))
            and self._persist_across_processes
            and not self._session_scoped
        ):
            if self._recreate_container():
                result = super().execute(command, cwd, **kwargs)
        return result

    def _build_init_env_args(self) -> list[str]:
        """Build -e KEY=VALUE args for injecting host env vars into init_session.

        These are used once during init_session() so that export -p captures
        them into the snapshot.  Subsequent execute() calls don't need -e flags.
        """
        exec_env: dict[str, str] = dict(self._env)

        explicit_forward_keys = set(self._forward_env)
        passthrough_keys: set[str] = set()
        try:
            from tools.env_passthrough import get_all_passthrough
            passthrough_keys = set(get_all_passthrough())
        except Exception:
            pass
        # Explicit docker_forward_env entries are an intentional opt-in and must
        # win over the generic Hermes secret blocklist. Only implicit passthrough
        # keys are filtered.
        forward_keys = explicit_forward_keys | (passthrough_keys - _HERMES_PROVIDER_ENV_BLOCKLIST)
        hermes_env = _load_hermes_env_vars() if forward_keys else {}
        for key in sorted(forward_keys):
            value = os.getenv(key)
            if value is None:
                value = hermes_env.get(key)
            if value is not None:
                exec_env[key] = value

        args = []
        for key in sorted(exec_env):
            args.extend(["-e", f"{key}={exec_env[key]}"])
        return args

    def _run_bash(self, cmd_string: str, *, login: bool = False,
                  timeout: int = 120,
                  stdin_data: str | None = None) -> subprocess.Popen:
        """Spawn a bash process inside the Docker container."""
        assert self._container_id, "Container not started"
        cmd = [self._docker_exe, "exec"]
        if stdin_data is not None:
            cmd.append("-i")

        # Only inject -e env args during init_session (login=True).
        # Subsequent commands get env vars from the snapshot.
        if login:
            cmd.extend(self._init_env_args)

        cmd.extend([self._container_id])

        if login:
            cmd.extend(["bash", "-l", "-c", cmd_string])
        else:
            cmd.extend(["bash", "-c", cmd_string])

        return _popen_bash(cmd, stdin_data)

    @staticmethod
    def _storage_opt_supported() -> bool:
        """Check if Docker's storage driver supports --storage-opt size=.
        
        Only overlay2 on XFS with pquota supports per-container disk quotas.
        Ubuntu (and most distros) default to ext4, where this flag errors out.
        """
        global _storage_opt_ok
        if _storage_opt_ok is not None:
            return _storage_opt_ok
        try:
            docker = find_docker() or "docker"
            result = subprocess.run(
                [docker, "info", "--format", "{{.Driver}}"],
                capture_output=True, text=True, timeout=10,
            )
            driver = result.stdout.strip().lower()
            if driver != "overlay2":
                _storage_opt_ok = False
                return False
            # overlay2 only supports storage-opt on XFS with pquota.
            # Probe by attempting a dry-ish run — the fastest reliable check.
            probe = subprocess.run(
                [docker, "create", "--storage-opt", "size=1m", "hello-world"],
                capture_output=True, text=True, timeout=15,
            )
            if probe.returncode == 0:
                # Clean up the created container
                container_id = probe.stdout.strip()
                if container_id:
                    subprocess.run([docker, "rm", container_id],
                                   capture_output=True, timeout=5)
                _storage_opt_ok = True
            else:
                _storage_opt_ok = False
        except Exception:
            _storage_opt_ok = False
        logger.debug("Docker --storage-opt support: %s", _storage_opt_ok)
        return _storage_opt_ok

    def cleanup(self):
        """Stop and remove the container. Bind-mount dirs persist if persistent=True."""
        preserve_for_next_process = bool(
            self._persist_across_processes
            and not self._session_scoped
        )
        if preserve_for_next_process:
            # Leave the labeled container alive for the next Hermes process.
            # The explicit orphan reaper remains a separate, future lifecycle
            # decision; clearing the in-process handle prevents stale execs.
            self._container_id = None
            return
        if self._container_id:
            try:
                # Stop in background so cleanup doesn't block
                stop_cmd = (
                    f"(timeout 60 {self._docker_exe} stop {self._container_id} || "
                    f"{self._docker_exe} rm -f {self._container_id}) >/dev/null 2>&1 &"
                )
                subprocess.Popen(stop_cmd, shell=True)
            except Exception as e:
                logger.warning("Failed to stop container %s: %s", self._container_id, e)

            if not self._persistent or self._session_scoped:
                # Also schedule removal (stop only leaves it as stopped)
                try:
                    subprocess.Popen(
                        f"sleep 3 && {self._docker_exe} rm -f {self._container_id} >/dev/null 2>&1 &",
                        shell=True,
                    )
                except Exception:
                    pass
            self._container_id = None

        if not self._persistent:
            for d in (self._workspace_dir, self._home_dir):
                if d:
                    shutil.rmtree(d, ignore_errors=True)
