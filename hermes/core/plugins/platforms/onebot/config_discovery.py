"""Bounded discovery of NapCat's account-specific OneBot11 credentials.

NapCat writes ``onebot11_<uin>.json`` after an account logs in.  The file is
the source of truth for the HTTP/WS access token; the token is a random
configuration secret, not a derivation of the QQ number.  This module only
reads explicitly local configuration files and returns a small in-memory
credential record.  It never writes `.env`, calls WebUI, or performs network
I/O.
"""

from __future__ import annotations

try:
    import hermes_bootstrap  # noqa: F401
except ModuleNotFoundError:
    hermes_bootstrap = None  # type: ignore[assignment]

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


_ACCOUNT_FILE_RE = re.compile(r"^onebot11_(\d{1,32})\.json$")
_PROTOCOL_FILE_RE = re.compile(r"^napcat_protocol_(\d{1,32})\.json$")
_MAX_CONFIG_BYTES = 2 * 1024 * 1024
_MAX_TOKEN_CHARS = 512
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True)
class NapCatOneBotCredentials:
    """Credentials selected from one account-specific NapCat config."""

    account_id: str
    token: str
    http_port: Optional[int] = None
    websocket_port: Optional[int] = None


@dataclass(frozen=True)
class NapCatOneBotAccountSummary:
    """Safe account summary for local Dashboard selection surfaces."""

    account_id: str
    available: bool
    token_configured: bool
    http_port: Optional[int] = None
    websocket_port: Optional[int] = None
    selected: bool = False


def _safe_regular_file(path: Path) -> bool:
    """Accept only a bounded regular file, rejecting symlink indirection."""

    try:
        info = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return False
    return 0 <= int(info.st_size) <= _MAX_CONFIG_BYTES


def _safe_mtime(path: Path) -> float:
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return float("-inf")


def _config_dirs() -> tuple[Path, ...]:
    """Return a small, deterministic set of operator-selected config roots."""

    candidates: list[Path] = []
    for env_name in ("ONEBOT_NAPCAT_CONFIG_DIR", "NAPCAT_CONFIG_DIR"):
        raw = os.getenv(env_name, "").strip()
        if raw:
            candidates.append(Path(raw).expanduser())

    cwd = Path.cwd()
    for root in (cwd, cwd.parent, cwd.parent.parent):
        candidates.append(root / "modules" / "napcat" / "napcat" / "config")

    # ``adapter.py`` lives at <bot>/hermes/core/plugins/platforms/onebot.
    # Keep this fixed-depth fallback narrow so importing the adapter never
    # scans a user's home directory or an entire drive.
    try:
        project_root = Path(__file__).resolve().parents[5]
    except (OSError, IndexError):
        project_root = None
    if project_root is not None:
        candidates.append(project_root / "modules" / "napcat" / "napcat" / "config")

    result: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            if candidate.is_symlink() or not candidate.is_dir():
                continue
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        result.append(resolved)
    return tuple(result)


def _account_files(config_dir: Path) -> dict[str, Path]:
    try:
        entries = list(config_dir.iterdir())
    except OSError:
        return {}
    result: dict[str, Path] = {}
    for path in entries:
        match = _ACCOUNT_FILE_RE.fullmatch(path.name)
        if not match or not _safe_regular_file(path):
            continue
        result[match.group(1)] = path
    return result


def _newest_protocol_account(config_dir: Path) -> Optional[str]:
    try:
        entries = list(config_dir.iterdir())
    except OSError:
        return None
    matches: list[tuple[float, str]] = []
    for path in entries:
        match = _PROTOCOL_FILE_RE.fullmatch(path.name)
        if match and _safe_regular_file(path):
            matches.append((_safe_mtime(path), match.group(1)))
    if not matches:
        return None
    matches.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return matches[0][1]


def _bounded_token(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    token = value.strip()
    if not token or len(token) > _MAX_TOKEN_CHARS:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in token):
        return None
    return token


def _bounded_port(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        port = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return port if 1 <= port <= 65_535 else None


def _enabled_loopback_servers(network: dict[str, Any], key: str) -> Iterable[dict[str, Any]]:
    servers = network.get(key)
    if not isinstance(servers, list):
        return ()
    result: list[dict[str, Any]] = []
    for server in servers:
        if not isinstance(server, dict) or not bool(server.get("enable")):
            continue
        host = str(server.get("host") or "").strip().lower()
        if host not in _LOOPBACK_HOSTS:
            continue
        result.append(server)
    return tuple(result)


def _enabled_servers_are_loopback(network: dict[str, Any]) -> bool:
    """Reject mixed/remote enabled servers during local auto-discovery."""

    for key in ("httpServers", "websocketServers"):
        servers = network.get(key)
        if not isinstance(servers, list):
            continue
        for server in servers:
            if not isinstance(server, dict) or not bool(server.get("enable")):
                continue
            host = str(server.get("host") or "").strip().lower()
            if host not in _LOOPBACK_HOSTS:
                return False
    return True


def _read_credentials(path: Path, account_id: str) -> Optional[NapCatOneBotCredentials]:
    if not _safe_regular_file(path):
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("network"), dict):
        return None
    network = payload["network"]
    if not _enabled_servers_are_loopback(network):
        return None
    http_servers = tuple(_enabled_loopback_servers(network, "httpServers"))
    websocket_servers = tuple(_enabled_loopback_servers(network, "websocketServers"))

    http_tokens = tuple(
        token
        for server in http_servers
        if (token := _bounded_token(server.get("token"))) is not None
    )
    websocket_tokens = tuple(
        token
        for server in websocket_servers
        if (token := _bounded_token(server.get("token"))) is not None
    )
    all_tokens = tuple(dict.fromkeys((*http_tokens, *websocket_tokens)))
    if len(all_tokens) != 1:
        # No enabled local server, or the HTTP/WS servers disagree. Either
        # case is unsafe to guess from during automatic discovery.
        return None

    http_port = next(
        (_bounded_port(server.get("port")) for server in http_servers),
        None,
    )
    websocket_port = next(
        (_bounded_port(server.get("port")) for server in websocket_servers),
        None,
    )
    return NapCatOneBotCredentials(
        account_id=account_id,
        token=all_tokens[0],
        http_port=http_port,
        websocket_port=websocket_port,
    )


def discover_napcat_onebot_credentials(
    self_id: Any = None,
    *,
    config_dir: str | Path | None = None,
) -> Optional[NapCatOneBotCredentials]:
    """Discover the active account's local OneBot11 token, if unambiguous.

    ``self_id`` is preferred when supplied.  Otherwise the newest
    ``napcat_protocol_<uin>.json`` marker selects the account, followed by the
    newest account-specific OneBot file.  A missing or malformed exact match
    returns ``None`` instead of silently borrowing another account's secret.
    """

    hinted_id: Optional[str] = None
    if isinstance(self_id, int) and not isinstance(self_id, bool):
        hinted_id = str(self_id)
    elif isinstance(self_id, str) and self_id.strip().isdigit():
        hinted_id = self_id.strip()
    directories = (Path(config_dir).expanduser(),) if config_dir is not None else _config_dirs()
    for directory in directories:
        try:
            if directory.is_symlink() or not directory.is_dir():
                continue
            directory = directory.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        files = _account_files(directory)
        if not files:
            continue
        account_id = hinted_id if hinted_id in files else None
        if hinted_id is not None and account_id is None:
            # An explicit self-id must never fall through to another account.
            continue
        if account_id is None:
            account_id = _newest_protocol_account(directory)
            if account_id not in files:
                account_id = max(files, key=lambda key: (_safe_mtime(files[key]), key))
        credentials = _read_credentials(files[account_id], account_id)
        if credentials is not None:
            return credentials
    return None


def list_napcat_onebot_accounts(
    selected_id: Any = None,
    *,
    config_dir: str | Path | None = None,
) -> tuple[NapCatOneBotAccountSummary, ...]:
    """List account-specific configs without returning secret material."""

    hinted_id: Optional[str] = None
    if isinstance(selected_id, int) and not isinstance(selected_id, bool):
        hinted_id = str(selected_id)
    elif isinstance(selected_id, str) and selected_id.strip().isdigit():
        hinted_id = selected_id.strip()

    directories = (Path(config_dir).expanduser(),) if config_dir is not None else _config_dirs()
    for directory in directories:
        try:
            if directory.is_symlink() or not directory.is_dir():
                continue
            directory = directory.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        files = _account_files(directory)
        if not files:
            continue
        active_id = hinted_id or _newest_protocol_account(directory)
        summaries: list[NapCatOneBotAccountSummary] = []
        for account_id, path in sorted(files.items()):
            credentials = _read_credentials(path, account_id)
            summaries.append(
                NapCatOneBotAccountSummary(
                    account_id=account_id,
                    available=credentials is not None,
                    token_configured=credentials is not None,
                    http_port=credentials.http_port if credentials else None,
                    websocket_port=credentials.websocket_port if credentials else None,
                    selected=account_id == active_id,
                )
            )
        return tuple(summaries)
    return ()


__all__ = [
    "NapCatOneBotAccountSummary",
    "NapCatOneBotCredentials",
    "discover_napcat_onebot_credentials",
    "list_napcat_onebot_accounts",
]
