"""
Hermes Dashboard — unified management backend.

Endpoints:
  GET  /              — dashboard HTML
  GET  /api/status    — full system status (memory, sessions, voice modes, OneBot)
  POST /api/voice     — set voice mode for a chat
  POST /api/memory    — trigger maintenance
  POST /api/memory/search — search all memory sources
  GET  /api/memory/workflows — workflow decay report
  GET  /api/memory/health    — comprehensive memory health (store, DB size, LTM categories, decay, retrieval)
  GET  /api/memory/timeline  — growth timeline over last 7 days
  GET  /api/obsidian  — obsidian vault stats
  POST /api/obsidian/search — search obsidian
  GET  /api/gateway/status  — gateway state, platforms, voice modes, session count
  GET  /api/sessions  — active sessions
  GET  /api/gateway/sessions — active sessions with chat_type
  POST /api/sessions/end — end a session
"""

from __future__ import annotations

import json
import logging
import sqlite3
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse, parse_qs
import urllib.request
from socketserver import ThreadingMixIn

DASHBOARD_DIR = Path(__file__).resolve().parent
BOT_DIR = DASHBOARD_DIR.parent.parent
STATIC_DIR = DASHBOARD_DIR / "static"
VENV_PYTHON = str(BOT_DIR / ".venv" / "Scripts" / "python.exe")

sys.path.insert(0, str(BOT_DIR / "hermes"))

from http.server import HTTPServer, BaseHTTPRequestHandler

logger = logging.getLogger("dashboard")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8899"))
HERMES_HOME = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))

SERVICES = {
    "tts_adapter": {
        "name": "TTS 适配器 (So-VITS)",
        "port": 5000,
        "cwd": str(BOT_DIR / "modules" / "tts"),
        "cmd": [VENV_PYTHON, "ts_adapter.py"],
        "color": "#FF6B35",
    },
    "napcat": {
        "name": "NapCat (QQ)",
        "port": 3000,
        "cwd": str(BOT_DIR / "napcat"),
        "cmd": ["cmd", "/c", "napcat.bat"],
        "color": "#34C759",
    },
    "hermes_gateway": {
        "name": "Hermes 网关",
        "port": 18789,
        "cwd": str(BOT_DIR),
        "cmd": [VENV_PYTHON, "-X", "utf8", "-m", "hermes_cli.main", "gateway"],
        "env": {"HERMES_HOME": os.path.expanduser("~/.hermes")},
        "color": "#007AFF",
    },
    "live2d": {
        "name": "Live2D (Cubism 4/5)",
        "port": 19919,
        "cwd": str(BOT_DIR / "modules" / "live2d"),
        "cmd": ["cmd", "/c", "npm", "start"],
        "color": "#AF52DE",
    },
    "local_vision": {
        "name": "本地视觉 (可选)",
        "port": 8010,
        "cwd": os.getenv("LOCAL_VISION_CWD", ""),
        "cmd": [
            os.getenv("LOCAL_VISION_EXE", "llama-server.exe"),
            "-m", os.getenv("LOCAL_VISION_MODEL", ""),
            "--mmproj", os.getenv("LOCAL_VISION_MMPROJ", ""),
            "--host", "127.0.0.1",
            "--port", "8010",
            "-ngl", "99",
            "-c", "2048",
            "--cont-batching",
            "--parallel", "1",
        ],
        "color": "#FF2D55",
    },
}

_running_processes: Dict[str, subprocess.Popen] = {}

LOGS: Dict[str, list] = {svc: [] for svc in SERVICES}
LOG_LOCK = threading.Lock()


def _build_timeline_days(stm_daily, ltm_daily, wiki_daily):
    """Merge three daily arrays into a unified days structure for the frontend."""
    from collections import defaultdict
    daily_map = defaultdict(lambda: {"facts_added": 0, "workflows_added": 0, "stm_entries": 0})
    for r in ltm_daily:
        daily_map[r["day"]]["facts_added"] += r["count"]
    for r in stm_daily:
        daily_map[r["day"]]["stm_entries"] += r["count"]
    for r in wiki_daily:
        daily_map[r["day"]]["workflows_added"] += r["count"]
    return sorted(
        [{"date": day, **counts} for day, counts in daily_map.items()],
        key=lambda d: d["date"]
    )


def _build_today_summary(stm_daily, ltm_daily, wiki_daily):
    """Build today's summary from daily data."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    facts = 0
    stm = 0
    wf = 0
    for r in ltm_daily:
        if r["day"] == today_str:
            facts += r["count"]
    for r in stm_daily:
        if r["day"] == today_str:
            stm += r["count"]
    for r in wiki_daily:
        if r["day"] == today_str:
            wf += r["count"]
    return {"facts_added": facts, "workflows_added": wf, "stm_entries": stm}


# Service log file paths (fallback when buffer is empty)
_SERVICE_LOG_FILES = {
    "hermes_gateway": HERMES_HOME / "logs" / "agent.log",
}


def _find_napcat_log():
    """Find the latest NapCat log file."""
    napcat_cwd = SERVICES.get("napcat", {}).get("cwd", "")
    napcat_base = Path(napcat_cwd) if napcat_cwd else None
    if not napcat_base or not napcat_base.exists():
        return None
    try:
        log_dir = napcat_base / "versions"
        if log_dir.exists():
            for d in sorted(log_dir.iterdir(), key=lambda p: p.name, reverse=True):
                log_path = d / "resources" / "app" / "napcat" / "logs" / "napcat.log"
                if log_path.exists():
                    return log_path
    except Exception:
        pass
    return None


def _get_log_lines(service: str, tail: int) -> list:
    with LOG_LOCK:
        if LOGS[service]:
            return LOGS[service][-tail:]

    # Fallback to file if buffer is empty
    if service in _SERVICE_LOG_FILES or service == "napcat":
        lines, _ = _tail_log_file(service, tail)
        if lines:
            return lines[-tail:]

    return []


def _tail_log_file(service: str, tail: int, byte_offset: int = 0):
    """Read new lines from a service's log file since byte_offset.
    Returns (new_lines, new_byte_offset). On first call pass byte_offset=0 to get last N lines.
    """
    file_path = _SERVICE_LOG_FILES.get(service)
    if service == "napcat":
        file_path = _find_napcat_log()
    if not file_path or not file_path.exists():
        return [], byte_offset
    try:
        file_size = file_path.stat().st_size
        if file_size < byte_offset:
            # File was truncated — re-read from start
            byte_offset = 0

        with open(file_path, "rb") as f:
            if byte_offset > 0:
                f.seek(byte_offset)
            raw = f.read()
        new_byte_offset = file_size

        text = raw.decode("utf-8", errors="replace")
        lines = [l for l in text.splitlines() if l.strip()]
        if not lines:
            return [], new_byte_offset

        now = datetime.now()
        result = []
        for line in lines:
            try:
                parts = line.split(" ", 2)
                if len(parts) >= 2:
                    t = f"{now.year}-{parts[0]} {parts[1]}"
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m/%d %H:%M:%S"):
                        try:
                            ts = datetime.strptime(t, fmt)
                            break
                        except ValueError:
                            ts = now
                    result.append({"ts": ts.isoformat(), "line": line})
            except Exception:
                result.append({"ts": now.isoformat(), "line": line})
        return result, new_byte_offset
    except Exception:
        return [], byte_offset


def _check_gateway_process():
    """Check if Hermes gateway process is running by scanning Python processes."""
    # Try HERMES_HOME state file first, then fallback to ~/.hermes
    state_paths = [
        HERMES_HOME / "gateway_state.json",
        Path.home() / ".hermes" / "gateway_state.json",
    ]
    state_says_running = False
    for state_path in state_paths:
        try:
            if state_path.exists():
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if state.get("gateway_state") in ("running", "starting"):
                    state_says_running = True
        except Exception:
            pass

    # Verify via dashboard-managed processes
    proc = _running_processes.get("hermes_gateway")
    if proc and proc.poll() is None:
        return True

    # Fast port check first (18789) — avoids the slow WMI scan in the
    # common case where the gateway is simply not running.
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        if sock.connect_ex(("127.0.0.1", 18789)) == 0:
            sock.close()
            return True
        sock.close()
    except Exception:
        pass

    # Fallback: scan Python command lines (only reached when gateway truly
    # down or port check failed — keep timeout short so status stays fast)
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command',
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Select-Object -ExpandProperty CommandLine"],
            capture_output=True, text=True, timeout=3,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
        )
        for line in result.stdout.splitlines():
            if 'hermes_cli.main' in line and 'gateway' in line:
                return True
    except Exception:
        pass

    # If process scan failed but state file says running, trust the file as fallback
    return state_says_running


def _kill_process_on_port(port: int):
    """Force kill any process listening on a given port."""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"$p = Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | "
             f"Select-Object -ExpandProperty OwningProcess -Unique; "
             f"if ($p) {{ Stop-Process -Id $p -Force }}"],
            capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        pass


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def _get_gateway():
    try:
        from agent.memory.gateway import UnifiedMemoryGateway
        return UnifiedMemoryGateway.get_instance()
    except Exception:
        return None


def _get_voice_modes() -> Dict:
    vp = HERMES_HOME / "gateway_voice_mode.json"
    try:
        return json.loads(vp.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_voice_modes(data: Dict):
    vp = HERMES_HOME / "gateway_voice_mode.json"
    vp.parent.mkdir(parents=True, exist_ok=True)
    vp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


class DashboardHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        logger.debug("%s - %s", self.client_address[0], format % args)

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))

    def _send_html(self, html):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _read_body(self) -> Dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError as e:
            logger.warning("Invalid JSON in request body: %s", e)
            self._send_json({"error": "Invalid JSON", "detail": str(e)}, 400)
            return {}

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        if path == "/" or path == "/index.html":
            return self._serve_index()

        elif path == "/api/status":
            return self._handle_status()

        elif path == "/api/memory/workflows":
            return self._handle_workflows()

        elif path == "/api/memory/health":
            return self._handle_memory_health()

        elif path == "/api/memory/timeline":
            return self._handle_memory_timeline()

        elif path == "/api/obsidian":
            return self._handle_obsidian_stats()

        elif path == "/api/services/status":
            return self._handle_services_status()

        elif path == "/api/services/logs":
            return self._handle_logs(params)

        elif path == "/api/services/logs/stream":
            return self._handle_logs_stream()

        elif path == "/api/gateway/status":
            return self._handle_gateway_status()

        elif path == "/api/gateway/sessions":
            return self._handle_gateway_sessions()

        elif path == "/api/sessions":
            return self._handle_sessions()

        elif path == "/api/live2d/models":
            return self._handle_live2d_models()

        elif path == "/api/live2d/default":
            return self._handle_live2d_default_get()

        elif path == "/api/napcat/port-status":
            return self._handle_napcat_port_status()

        elif path == "/api/updates/check":
            return self._handle_updates_check()

        elif path == "/api/onboarding/status":
            return self._handle_onboarding_status()

        # Static file serving (onboarding.html, etc.)
        elif "." in path.split("/")[-1]:
            return self._serve_static(path)

        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._read_body()

        if path == "/api/voice":
            return self._handle_voice_set(body)

        elif path == "/api/services/start":
            return self._handle_services_start(body)

        elif path == "/api/services/stop":
            return self._handle_services_stop(body)

        elif path == "/api/services/restart":
            return self._handle_services_restart(body)

        elif path == "/api/memory":
            return self._handle_memory_action(body)

        elif path == "/api/memory/search":
            return self._handle_memory_search(body)

        elif path == "/api/obsidian/search":
            return self._handle_obsidian_search(body)

        elif path == "/api/sessions/end":
            return self._handle_session_end(body)

        elif path == "/api/napcat/start":
            return self._handle_napcat_start()

        elif path == "/api/napcat/stop":
            return self._handle_napcat_stop()

        elif path == "/api/napcat/anti-detection":
            return self._handle_napcat_anti_detection()

        elif path == "/api/live2d/models":
            return self._handle_live2d_models()

        elif path == "/api/live2d/switch":
            return self._handle_live2d_switch(body)

        elif path == "/api/live2d/default":
            if self.command == "GET":
                return self._handle_live2d_default_get()
            else:
                return self._handle_live2d_default_set(body)

        else:
            self._send_json({"error": "Not found"}, 404)

    # ── Pages ─────────────────────────────────────────────────

    def _serve_index(self):
        try:
            html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        except Exception:
            html = "<h1>Dashboard</h1><p>Static files not found.</p>"
        self._send_html(html)

    def _serve_static(self, path: str):
        """Serve static files from STATIC_DIR."""
        safe_path = path.lstrip("/")
        filepath = STATIC_DIR / safe_path
        # Prevent directory traversal
        try:
            filepath.resolve().relative_to(STATIC_DIR.resolve())
        except ValueError:
            self._send_json({"error": "Forbidden"}, 403)
            return
        if not filepath.is_file():
            self._send_json({"error": "Not found"}, 404)
            return
        ext = filepath.suffix.lower()
        ct = {".html": "text/html; charset=utf-8", ".js": "application/javascript",
              ".css": "text/css", ".json": "application/json", ".png": "image/png",
              ".svg": "image/svg+xml"}.get(ext, "application/octet-stream")
        try:
            data = filepath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            self._send_json({"error": "Read error"}, 500)

    # ── Updates check ─────────────────────────────────────────

    def _handle_updates_check(self):
        """Check GitHub for latest release version."""
        import urllib.request
        try:
            url = "https://api.github.com/repos/jixiong398-blip/hermes-for--qqbot/releases/latest"
            req = urllib.request.Request(url, headers={"User-Agent": "QQBot-Dashboard"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            latest = data.get("tag_name", "").lstrip("v")
            current = ""
            version_file = BOT_DIR / "VERSION"
            if version_file.exists():
                current = version_file.read_text(encoding="utf-8").strip()
            has_update = latest and latest != current
            self._send_json({
                "has_update": has_update,
                "current_version": current,
                "latest_version": latest,
                "release_url": data.get("html_url", ""),
            })
        except Exception as e:
            self._send_json({"has_update": False, "error": str(e)}, 200)

    # ── Onboarding ────────────────────────────────────────────

    def _handle_onboarding_status(self):
        """Check if onboarding is needed."""
        cfg = HERMES_HOME / "config.yaml"
        env = HERMES_HOME / ".env"
        soul = HERMES_HOME / "SOUL.md"
        self._send_json({
            "config_exists": cfg.exists(),
            "env_exists": env.exists(),
            "soul_exists": soul.exists(),
            "venv_exists": (BOT_DIR / ".venv" / "Scripts" / "python.exe").exists(),
            "napcat_exists": (BOT_DIR / "napcat" / "napcat.bat").exists(),
        })

    # ── Status ────────────────────────────────────────────────

    def _handle_status(self):
        gw = _get_gateway()
        voice_modes = _get_voice_modes()

        result = {
            "timestamp": datetime.now().isoformat(),
            "gateway": {
                "port": os.getenv("GATEWAY_PORT", "18789"),
                "running": _check_gateway_process(),
            },
            "voice_modes": voice_modes,
        }

        if gw:
            stats = gw.get_stats()
            result["memory"] = {
                "short_term": stats["store"]["short_term_count"],
                "long_term": stats["store"]["long_term_count"],
                "workflows": stats["store"]["workflow_count"],
                "wiki_chunks": stats["store"]["wiki_chunk_count"],
                "active_sessions": stats["active_sessions"],
                "decay_enabled": stats["workflow_decay_enabled"],
            }
            result["obsidian"] = gw.get_obsidian_stats()
        else:
            result["memory"] = {"error": "Memory system not initialized"}

        # OneBot status
        try:
            # Try to load token from hermes .env
            onebot_token = os.getenv("ONEBOT_ACCESS_TOKEN", "")
            if not onebot_token:
                env_path = HERMES_HOME / ".env"
                if env_path.exists():
                    for line in env_path.read_text(encoding="utf-8").splitlines():
                        if line.startswith("ONEBOT_ACCESS_TOKEN="):
                            onebot_token = line.split("=", 1)[1].strip()
                            break
            req = urllib.request.Request("http://127.0.0.1:3000/get_login_info")
            req.add_header("Authorization", f"Bearer {onebot_token}")
            with urllib.request.urlopen(req, timeout=3) as resp:
                login_info = json.loads(resp.read().decode())
                result["onebot"] = {
                    "connected": login_info.get("status") == "ok",
                    "user_id": login_info.get("data", {}).get("user_id", ""),
                    "nickname": login_info.get("data", {}).get("nickname", ""),
                }
        except Exception:
            result["onebot"] = {"connected": False, "error": "NapCat not reachable"}

        self._send_json(result)

    # ── Voice Mode ────────────────────────────────────────────

    def _handle_voice_set(self, body: Dict):
        chat_id = body.get("chat_id", "")
        mode = body.get("mode", "pure_voice")
        if not chat_id:
            return self._send_json({"error": "chat_id required"}, 400)
        if mode not in ("off", "voice_only", "all", "pure_voice"):
            return self._send_json({"error": f"Invalid mode: {mode}"}, 400)

        modes = _get_voice_modes()
        modes[str(chat_id)] = mode
        _save_voice_modes(modes)
        self._send_json({"success": True, "chat_id": chat_id, "mode": mode})

    # ── Memory ────────────────────────────────────────────────

    def _handle_memory_action(self, body: Dict):
        action = body.get("action", "")
        gw = _get_gateway()
        if not gw:
            return self._send_json({"error": "Memory not available"}, 503)

        if action == "maintenance":
            stats = gw.maintenance_cycle()
            return self._send_json({"success": True, "stats": stats})
        elif action == "consolidate":
            sid = body.get("session_id", "")
            if sid:
                stats = gw.consolidate(sid)
            else:
                stats = {"error": "session_id required"}
            return self._send_json({"success": True, "stats": stats})
        else:
            return self._send_json({"error": f"Unknown action: {action}"}, 400)

    def _handle_memory_search(self, body: Dict):
        query = body.get("query", "")
        if not query:
            return self._send_json({"error": "query required"}, 400)

        gw = _get_gateway()
        if not gw:
            return self._send_json({"error": "Memory not available"}, 503)

        ltm = gw.search_long_term(query, 10)
        wfm = gw.search_workflows(query)
        return self._send_json({
            "query": query,
            "long_term": ltm,
            "workflows": wfm,
        })

    def _handle_workflows(self):
        gw = _get_gateway()
        if not gw:
            return self._send_json({"error": "Memory not available"}, 503)
        report = gw.get_workflow_decay_report()
        self._send_json(report)

    # ── Obsidian ──────────────────────────────────────────────

    def _handle_obsidian_stats(self):
        gw = _get_gateway()
        if not gw:
            return self._send_json({"error": "Memory not available"}, 503)
        try:
            gw.index_obsidian()
        except Exception:
            pass
        self._send_json(gw.get_obsidian_stats())

    def _handle_obsidian_search(self, body: Dict):
        query = body.get("query", "")
        if not query:
            return self._send_json({"error": "query required"}, 400)
        gw = _get_gateway()
        if not gw:
            return self._send_json({"error": "Memory not available"}, 503)
        try:
            gw.index_obsidian()
        except Exception:
            pass
        results = gw.search_obsidian(query, top_k=10)
        self._send_json({"query": query, "results": results})

    # ── Sessions ──────────────────────────────────────────────

    def _handle_sessions(self):
        sessions_dir = HERMES_HOME / "sessions"
        sessions = []
        if sessions_dir.exists():
            for f in sorted(sessions_dir.glob("*.json"), key=lambda x: -x.stat().st_mtime):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    sessions.append({
                        "session_id": f.stem,
                        "platform": data.get("platform", ""),
                        "chat_id": data.get("chat_id", ""),
                        "chat_type": data.get("chat_type", data.get("origin", {}).get("chat_type", "dm")),
                        "message_count": len(data.get("messages", [])),
                        "updated": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                    })
                except Exception:
                    pass
        self._send_json(sessions[:20])

    def _handle_gateway_sessions(self):
        try:
            sessions_dir = HERMES_HOME / "sessions"
            sessions = []
            sessions_file = sessions_dir / "sessions.json"
            if sessions_file.exists():
                try:
                    index_data = json.loads(sessions_file.read_text(encoding="utf-8"))
                    for session_key, entry_data in index_data.items():
                        if not isinstance(entry_data, dict):
                            continue
                        sid = entry_data.get("session_id", "")
                        # Try to get message count from transcript
                        msg_count = 0
                        if sid:
                            transcript = sessions_dir / f"session_{sid}.json"
                            if transcript.exists():
                                try:
                                    tdata = json.loads(transcript.read_text(encoding="utf-8"))
                                    msg_count = len(tdata.get("messages", []))
                                except Exception:
                                    pass
                        sessions.append({
                            "session_id": sid,
                            "session_key": session_key,
                            "platform": entry_data.get("platform", ""),
                            "chat_type": entry_data.get("chat_type", "dm"),
                            "display_name": entry_data.get("display_name"),
                            "message_count": msg_count,
                            "last_activity": entry_data.get("updated_at", ""),
                        })
                except Exception:
                    pass
            self._send_json(sessions[:50])
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_session_end(self, body: Dict):
        sid = body.get("session_id", "")
        if not sid:
            return self._send_json({"error": "session_id required"}, 400)
        gw = _get_gateway()
        if gw:
            gw.on_session_end(sid)
        self._send_json({"success": True, "session_id": sid})

    # ── Gateway Status ────────────────────────────────────────

    def _handle_gateway_status(self):
        try:
            # Check if Hermes gateway is running via process detection
            gateway_running = _check_gateway_process()
            if not gateway_running:
                gateway_running = self._check_port(18789)

            state_path = HERMES_HOME / "gateway_state.json"
            state_data = {}
            try:
                if state_path.exists():
                    state_data = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                pass

            voice_modes = _get_voice_modes()

            sessions_dir = HERMES_HOME / "sessions"
            session_count = 0
            if sessions_dir.exists():
                sessions_file = sessions_dir / "sessions.json"
                if sessions_file.exists():
                    try:
                        data = json.loads(sessions_file.read_text(encoding="utf-8"))
                        if isinstance(data, dict):
                            session_count = len(data)
                    except Exception:
                        pass

            platforms = state_data.get("platforms", {})
            clean_platforms = {}
            for name, pinfo in platforms.items():
                if isinstance(pinfo, dict):
                    clean_platforms[name] = {
                        "state": pinfo.get("state", "unknown"),
                        "error": pinfo.get("error_message"),
                    }

            result = {
                "running": gateway_running,
                "gateway_running": gateway_running,
                "state": state_data.get("gateway_state", "unknown") if gateway_running else "stopped",
                "active_agents": state_data.get("active_agents", 0),
                "platforms": list(clean_platforms.keys()),
                "exit_reason": state_data.get("exit_reason"),
                "voice_modes": voice_modes,
                "session_count": session_count,
            }
            # Compute uptime from started_at
            started_at = state_data.get("started_at")
            if started_at and gateway_running:
                result["started_at"] = started_at
                try:
                    dt = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
                    delta = datetime.now().astimezone() - dt
                    hours = int(delta.total_seconds() // 3600)
                    mins = int((delta.total_seconds() % 3600) // 60)
                    result["uptime"] = f"{hours}h {mins}m" if hours > 0 else f"{mins}m"
                except Exception:
                    result["uptime"] = "--"
            else:
                result["started_at"] = None
                result["uptime"] = "--"
            self._send_json(result)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ── Memory Health ─────────────────────────────────────────

    def _handle_memory_health(self):
        try:
            gw = _get_gateway()
            if not gw:
                return self._send_json({"error": "Memory not available"}, 503)

            stats = gw.get_stats()
            decay_report = gw.get_workflow_decay_report()

            db_path = HERMES_HOME / "memory_store.db"
            db_size_mb = round(db_path.stat().st_size / (1024 * 1024), 2) if db_path.exists() else 0

            ltm_categories = []
            total_retrievals = 0
            top_retrieved = []

            if db_path.exists():
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                try:
                    rows = conn.execute(
                        "SELECT category, COUNT(*) as cnt, AVG(confidence) as avg_conf "
                        "FROM long_term_entries GROUP BY category"
                    ).fetchall()
                    ltm_categories = [
                        {
                            "category": r["category"],
                            "count": r["cnt"],
                            "avg_confidence": round(r["avg_conf"], 3),
                        }
                        for r in rows
                    ]

                    total_retrievals = conn.execute(
                        "SELECT COALESCE(SUM(retrieval_count), 0) FROM long_term_entries"
                    ).fetchone()[0]

                    top_rows = conn.execute(
                        "SELECT category, key, value, retrieval_count "
                        "FROM long_term_entries ORDER BY retrieval_count DESC LIMIT 5"
                    ).fetchall()
                    top_retrieved = [
                        {
                            "category": r["category"],
                            "key": r["key"],
                            "value": r["value"][:200],
                            "retrieval_count": r["retrieval_count"],
                        }
                        for r in top_rows
                    ]
                finally:
                    conn.close()

            active = sum(1 for w in decay_report if w.get("status") == "active")
            decaying = sum(1 for w in decay_report if w.get("status") == "decaying")
            forgotten = sum(1 for w in decay_report if w.get("status") == "forgotten")

            # Compute 24h growth
            ltm_growth_24h = 0
            if db_path.exists():
                cutoff_24h = (datetime.now() - timedelta(hours=24)).timestamp()
                conn2 = sqlite3.connect(str(db_path))
                try:
                    row = conn2.execute(
                        "SELECT COUNT(*) FROM long_term_entries WHERE created_at > ?",
                        (cutoff_24h,),
                    ).fetchone()
                    ltm_growth_24h = row[0] if row else 0
                finally:
                    conn2.close()

            # Build flat category dict for frontend
            ltm_by_flat = {r["category"]: r["count"] for r in ltm_categories} if ltm_categories else {}

            # Last consolidation timestamp
            consolidation_path = HERMES_HOME / "consolidation_state.json"
            last_consolidation = None
            if consolidation_path.exists():
                try:
                    cs = json.loads(consolidation_path.read_text(encoding="utf-8"))
                    last_consolidation = cs.get("last_consolidation")
                except Exception:
                    pass

            result = {
                "store": {
                    "short_term": stats["store"]["short_term_count"],
                    "long_term": stats["store"]["long_term_count"],
                    "workflows": stats["store"]["workflow_count"],
                    "wiki_chunks": stats["store"]["wiki_chunk_count"],
                },
                "database_size_mb": db_size_mb,
                # Flat fields for frontend readability
                "ltm_facts": stats["store"]["long_term_count"],
                "ltm_total": stats["store"]["long_term_count"],
                "ltm_growth_24h": ltm_growth_24h,
                "stm_active": stats["store"]["short_term_count"],
                "db_size_mb": db_size_mb,
                "ltm_by_category": ltm_by_flat,
                "workflows_active": active,
                "workflows_decaying": decaying,
                "workflows_forgotten": forgotten,
                "last_consolidation": last_consolidation,
                # Nested structure preserved for detailed views
                "ltm_categories": ltm_categories,
                "workflow_decay": {
                    "total": len(decay_report),
                    "active": active,
                    "decaying": decaying,
                    "forgotten": forgotten,
                },
                "retrieval_stats": {
                    "total_ltm_retrievals": total_retrievals,
                    "top_retrieved_facts": top_retrieved,
                },
            }
            self._send_json(result)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ── Memory Timeline ───────────────────────────────────────

    def _handle_memory_timeline(self):
        try:
            db_path = HERMES_HOME / "memory_store.db"
            if not db_path.exists():
                return self._send_json({"stm_daily": [], "ltm_daily": [], "wiki_daily": []})

            cutoff = (datetime.now() - timedelta(days=7)).timestamp()

            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                stm_rows = conn.execute(
                    "SELECT date(created_at, 'unixepoch') as day, COUNT(*) as cnt "
                    "FROM short_term_entries WHERE created_at > ? "
                    "GROUP BY day ORDER BY day",
                    (cutoff,),
                ).fetchall()
                stm_daily = [{"day": r["day"], "count": r["cnt"]} for r in stm_rows]

                ltm_rows = conn.execute(
                    "SELECT date(created_at, 'unixepoch') as day, COUNT(*) as cnt "
                    "FROM long_term_entries WHERE created_at > ? "
                    "GROUP BY day ORDER BY day",
                    (cutoff,),
                ).fetchall()
                ltm_daily = [{"day": r["day"], "count": r["cnt"]} for r in ltm_rows]

                wiki_rows = conn.execute(
                    "SELECT date(created_at, 'unixepoch') as day, COUNT(*) as cnt "
                    "FROM wiki_entries WHERE created_at > ? "
                    "GROUP BY day ORDER BY day",
                    (cutoff,),
                ).fetchall()
                wiki_daily = [{"day": r["day"], "count": r["cnt"]} for r in wiki_rows]
            finally:
                conn.close()

            self._send_json({
                "stm_daily": stm_daily,
                "ltm_daily": ltm_daily,
                "wiki_daily": wiki_daily,
                # Merged format for frontend
                "days": _build_timeline_days(stm_daily, ltm_daily, wiki_daily),
                "today": _build_today_summary(stm_daily, ltm_daily, wiki_daily),
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ── Live2D Model Control ──────────────────────────────────

    LIVE2D_PREF_FILE = BOT_DIR / "modules" / "live2d" / "live2d_pref.json"

    def _read_live2d_default(self):
        """Read saved character/costume from live2d_pref.json, or return defaults."""
        try:
            if self.LIVE2D_PREF_FILE.exists():
                data = json.loads(self.LIVE2D_PREF_FILE.read_text(encoding="utf-8"))
                return data.get("character", "soyo"), data.get("costume", "casual_spring_01")
        except Exception:
            pass
        return "soyo", "casual_spring_01"

    def _save_live2d_default(self, character: str, costume: str):
        """Save character/costume preference."""
        self.LIVE2D_PREF_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.LIVE2D_PREF_FILE.write_text(
            json.dumps({"character": character, "costume": costume}, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def _handle_live2d_default_get(self):
        """Return current default character/costume."""
        char, cos = self._read_live2d_default()
        self._send_json({"character": char, "costume": cos})

    def _handle_live2d_default_set(self, body: Dict):
        """Save default character/costume."""
        character = str(body.get("character", "soyo")).strip()
        costume = str(body.get("costume", "casual_spring_01")).strip()
        if not character or not costume:
            self._send_json({"error": "character and costume required"}, 400)
            return
        self._save_live2d_default(character, costume)
        self._send_json({"ok": True, "character": character, "costume": costume})

    def _handle_live2d_models(self):
        """Return list of available characters and their outfits from Cubism 4/5 models."""
        models = {}
        default_char, default_costume = self._read_live2d_default()

        # Scan Cubism 4/5 models
        l2d_models = str(BOT_DIR / "modules" / "live2d" / "assets" / "models")
        if Path(l2d_models).exists():
            for char_dir in sorted(Path(l2d_models).iterdir()):
                if not char_dir.is_dir():
                    continue
                outfits = []
                for o_dir in sorted(char_dir.iterdir()):
                    if not o_dir.is_dir() or o_dir.name.startswith('.'):
                        continue
                    has_model = any(f.name.endswith(".model3.json") for f in o_dir.glob("*.json"))
                    has_moc3 = any(o_dir.glob("*.moc3"))
                    if has_model or has_moc3:
                        outfits.append(o_dir.name)
                if outfits:
                    models[char_dir.name] = {
                        "name": char_dir.name,
                        "outfits": outfits,
                        "format": "cubism4",
                    }

        self._send_json({
            "characters": models,
            "default": {"character": default_char, "costume": default_costume},
            "empty": len(models) == 0,
            "hint": "No models installed. Click 'Manage Models' to download from CDN." if len(models) == 0 else None,
        })

    def _handle_live2d_switch(self, body: Dict):
        """Switch Live2D character/outfit via HTTP, and save as default."""
        character = body.get("character", "soyo")
        costume = body.get("costume", "casual_spring_01")
        try:
            import urllib.request
            data = json.dumps({"type": "switch_model", "character": character, "costume": costume}).encode()
            req = urllib.request.Request("http://127.0.0.1:19919/cmd", data=data,
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
            self._save_live2d_default(character, costume)
            self._send_json({"ok": True, "character": character, "costume": costume, "saved": True})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # ── NapCat Control ────────────────────────────────────────

    def _handle_napcat_start(self):
        napcat_dir = str(BOT_DIR / "napcat")
        try:
            proc = subprocess.Popen(
                ["cmd", "/c", "napcat.bat"],
                cwd=napcat_dir,
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
            _running_processes["napcat"] = proc
            self._send_json({"success": True, "message": "NapCat launching in new window", "pid": proc.pid})
        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, 500)

    def _handle_napcat_stop(self):
        try:
            killed = False
            # Method 1: Kill by process pattern
            for pattern in ('*NapCat.Shell*', '*NapCatWinBootMain*'):
                try:
                    subprocess.run(
                        ["powershell", "-NoProfile", "-Command",
                         "Get-CimInstance Win32_Process | "
                         f"Where-Object {{ $_.CommandLine -like '{pattern}' }} | "
                         "ForEach-Object { taskkill /F /PID $_.ProcessId /T }"],
                        capture_output=True, timeout=20,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                except Exception:
                    pass
            # Method 2: Kill by port (always works regardless of command line)
            for port in (3000, 3001):
                try:
                    subprocess.run(
                        ["powershell", "-NoProfile", "-Command",
                         f"$p = Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | "
                         "Select-Object -ExpandProperty OwningProcess; "
                         "if ($p) { Stop-Process -Id $p -Force }"],
                        capture_output=True, timeout=10,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                except Exception:
                    pass
            # Method 3: Kill cmd.exe running napcat.bat
            try:
                subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Get-CimInstance Win32_Process -Filter \"Name='cmd.exe'\" | "
                     "Where-Object { $_.CommandLine -like '*napcat*' -and $_.CommandLine -like '*NapCat*' } | "
                     "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                    capture_output=True, timeout=10,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except Exception:
                pass
            self._send_json({"success": True, "message": "NapCat stopped"})
        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, 500)

    def _handle_napcat_port_status(self):
        """Check if NapCat WS/HTTP ports are open."""
        ws_ok = self._check_port(3001)
        http_ok = self._check_port(3000)
        self._send_json({
            "ws_3001": ws_ok,
            "http_3000": http_ok,
            "napcat_running": self._check_port(6099),
        })

    def _handle_napcat_anti_detection(self):
        """Enable anti-detection settings in NapCat config."""
        try:
            cfg_dir = BOT_DIR / "napcat" / "napcat" / "config"
            # Detect current QQ from newest protocol file
            qq = None
            protocol_files = sorted(cfg_dir.glob("napcat_protocol_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
            for f in protocol_files:
                m = __import__("re").match(r"napcat_protocol_(\d+)", f.name)
                if m: qq = m.group(1); break
            if not qq:
                self._send_json({"success": False, "error": "No logged-in QQ found"}, 400)
                return
            f = cfg_dir / f"napcat_{qq}.json"
            if f.exists():
                cfg = __import__("json").loads(f.read_text(encoding="utf-8"))
                # Enable all anti-detection settings
                cfg["o3HookMode"] = 1
                cfg["autoTimeSync"] = True
                cfg["bypass"] = {
                    "hook": True,
                    "window": True,
                    "module": True,
                    "process": True,
                    "container": True,
                    "js": True,
                }
                f.write_text(__import__("json").dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
                self._send_json({"success": True, "message": f"Anti-detection fully enabled for QQ {qq}"})
                return
            self._send_json({"success": False, "error": "No napcat config found"}, 400)
        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, 500)

    # ── Service Management ────────────────────────────────────

    @classmethod
    def _check_port(cls, port: int) -> bool:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.5)
            result = sock.connect_ex(("127.0.0.1", port)) == 0
            sock.close()
            return result
        except Exception:
            return False

    def _handle_services_start(self, body: Dict):
        service = body.get("service", "")
        if not service:
            return self._send_json({"error": "service required"}, 400)
        if service == "all":
            results = []
            # Each _start_one_service self-checks for existing instance — no
            # need to pre-kill everything (that was the slow path).
            results.append(self._start_one_service("tts_adapter"))
            results.append(self._start_one_service("napcat"))
            results.append(self._start_one_service("hermes_gateway"))
            results.append(self._start_one_service("live2d"))
            # local_vision uses ~6GB VRAM — only start when needed for NSFW fallback
            # results.append(self._start_one_service("local_vision"))
            return self._send_json({"success": True, "results": results})
        if service not in SERVICES:
            return self._send_json({"error": f"Unknown service: {service}"}, 400)
        # Async start: return immediately, let the background thread do the
        # (potentially slow) kill + Popen so the UI gets a sub-second response.
        def _bg_start():
            try:
                self._start_one_service(service)
            except Exception:
                pass
        t = threading.Thread(target=_bg_start, daemon=True)
        t.start()
        return self._send_json({"service": service, "status": "starting", "async": True})

    def _handle_services_stop(self, body: Dict):
        service = body.get("service", "")
        if not service:
            return self._send_json({"error": "service required"}, 400)
        if service == "all":
            results = []
            for key in SERVICES:
                results.append(self._stop_one_service(key))
            return self._send_json({"success": True, "results": results})
        if service not in SERVICES:
            return self._send_json({"error": f"Unknown service: {service}"}, 400)
        # Async stop: return immediately, background thread does the kill.
        def _bg_stop():
            try:
                self._stop_one_service(service)
            except Exception:
                pass
        t = threading.Thread(target=_bg_stop, daemon=True)
        t.start()
        return self._send_json({"service": service, "status": "stopping", "async": True})

    def _handle_services_restart(self, body: Dict):
        service = body.get("service", "")
        if not service:
            return self._send_json({"error": "service required"}, 400)
        if service not in SERVICES:
            return self._send_json({"error": f"Unknown service: {service}"}, 400)
        self._stop_one_service(service)
        time.sleep(0.3)
        result = self._start_one_service(service)
        return self._send_json(result)

    def _handle_services_status(self):
        result = {"services": {}}
        for key, svc in SERVICES.items():
            is_running = self._check_port(svc["port"])
            if not is_running and key == "napcat":
                is_running = self._check_port(3001)
            if not is_running and key == "hermes_gateway":
                is_running = _check_gateway_process()
            if not is_running:
                proc = _running_processes.get(key)
                if proc and proc.poll() is None:
                    is_running = True
            result["services"][key] = {
                "name": svc["name"],
                "port": svc["port"],
                "status": "running" if is_running else "stopped",
                "color": svc["color"],
            }
        self._send_json(result)

    def _handle_logs(self, params: Dict):
        service = params.get("service", "all")
        try:
            tail = int(params.get("tail", 50))
        except (ValueError, TypeError):
            tail = 50

        now = datetime.now().isoformat()

        if service == "all":
            result = {}
            for svc in SERVICES:
                lines = _get_log_lines(svc, tail)
                result[svc] = lines
            self._send_json({"service": "all", "logs": result, "tail": tail})
        elif service in SERVICES:
            lines = _get_log_lines(service, tail)
            self._send_json({"service": service, "lines": lines, "count": len(lines), "tail": tail})
        else:
            self._send_json({"error": f"Unknown service: {service}"}, 400)

    def _handle_logs_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        # Send buffered lines from dashboard-launched services on connect
        with LOG_LOCK:
            for svc in SERVICES:
                for entry in LOGS[svc][-50:]:
                    data = json.dumps({
                        "service": svc,
                        "line": entry["line"],
                        "ts": entry["ts"],
                    }, ensure_ascii=False)
                    self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
        self.wfile.flush()

        # Track buffer positions (line count) for dashboard-launched services
        positions = {svc: len(LOGS[svc]) for svc in SERVICES}

        # Track byte offsets for file-based services (hermes, napcat)
        # Start at file end — only deliver new writes
        file_offsets = {}
        for svc in _SERVICE_LOG_FILES:
            p = _SERVICE_LOG_FILES[svc]
            if p.exists():
                file_offsets[svc] = p.stat().st_size
        napcat_log = _find_napcat_log()
        if napcat_log and napcat_log.exists():
            file_offsets["napcat"] = napcat_log.stat().st_size

        last_heartbeat = time.time()

        try:
            while True:
                time.sleep(2)
                has_new = False

                # 1. Buffer lines from dashboard-launched processes
                with LOG_LOCK:
                    for svc in SERVICES:
                        new_lines = LOGS[svc][positions[svc]:]
                        for entry in new_lines:
                            data = json.dumps({
                                "service": svc,
                                "line": entry["line"],
                                "ts": entry["ts"],
                            }, ensure_ascii=False)
                            self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                            has_new = True
                        if new_lines:
                            positions[svc] = len(LOGS[svc])

                # 2. File-based: read new bytes since last offset
                for svc in ("hermes_gateway", "napcat"):
                    offset = file_offsets.get(svc, 0)
                    new_lines, new_offset = _tail_log_file(svc, 0, offset)
                    if new_lines:
                        for entry in new_lines:
                            data = json.dumps({
                                "service": svc,
                                "line": entry["line"],
                                "ts": entry["ts"],
                            }, ensure_ascii=False)
                            self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                            has_new = True
                    file_offsets[svc] = new_offset

                if time.time() - last_heartbeat >= 15:
                    self.wfile.write(b": heartbeat\n\n")
                    last_heartbeat = time.time()

                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass  # client disconnected — exit the stream

    def _start_one_service(self, key: str) -> Dict:
        svc = SERVICES[key]
        try:
            # ── Hermes gateway prerequisite: NapCat must be up ──
            # Before starting Hermes, verify NapCat is reachable (process or
            # WS/HTTP ports 3000/3001). Log the check result; if NapCat is
            # down, return a special marker so the UI can prompt the user.
            if key == "hermes_gateway":
                napcat_up = False
                # 1) NapCat process check
                napcat_proc = _running_processes.get("napcat")
                napcat_proc_alive = napcat_proc is not None and napcat_proc.poll() is None
                # 2) Port check: 3000 (HTTP) and 3001 (WS)
                napcat_http = self._check_port(3000)
                napcat_ws = self._check_port(3001)
                napcat_up = napcat_proc_alive or napcat_http or napcat_ws
                logger.info(
                    "[Dashboard] Hermes start pre-check: napcat_proc=%s http3000=%s ws3001=%s -> %s",
                    napcat_proc_alive, napcat_http, napcat_ws,
                    "READY" if napcat_up else "NAPCAT_NOT_RUNNING",
                )
                if not napcat_up:
                    return {
                        "service": key,
                        "name": svc["name"],
                        "status": "error",
                        "need_napcat": True,
                        "error": "NapCat is not running. Please start NapCat (QQ) first, then start Hermes.",
                    }

            # Kill existing instance ONLY if we have one tracked or port is busy.
            # Quick port check avoids the expensive PowerShell kill path when
            # nothing is actually running (the common case -> fast start).
            port_busy = self._check_port(svc["port"])
            if key == "napcat" and not port_busy:
                port_busy = self._check_port(3001)
            existing = _running_processes.get(key)
            if (existing is not None and existing.poll() is None) or port_busy:
                self._force_kill_service(key)
                time.sleep(0.2)

            kwargs: Dict[str, Any] = {
                "cwd": svc["cwd"],
                "creationflags": subprocess.CREATE_NO_WINDOW,
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "text": True,
                "bufsize": 1,
                "encoding": "utf-8",
                "errors": "replace",
            }
            if "env" in svc:
                new_env = os.environ.copy()
                new_env.update(svc["env"])
                kwargs["env"] = new_env
            proc = subprocess.Popen(svc["cmd"], **kwargs)
            _running_processes[key] = proc

            def _read_output():
                try:
                    for line in proc.stdout:
                        line = line.rstrip("\n\r")
                        if not line:
                            continue
                        entry = {"ts": datetime.now().isoformat(), "line": line}
                        with LOG_LOCK:
                            LOGS[key].append(entry)
                            if len(LOGS[key]) > 200:
                                LOGS[key] = LOGS[key][-200:]
                except Exception:
                    pass

            t = threading.Thread(target=_read_output, daemon=True)
            t.start()

            return {"service": key, "name": svc["name"], "status": "starting", "pid": proc.pid}
        except Exception as e:
            return {"service": key, "name": svc["name"], "status": "error", "error": str(e)}

    def _force_kill_service(self, key: str):
        """Kill any running instance of a service by its process pattern."""
        patterns = {
            "tts_adapter": ["ts_adapter.py"],
            "napcat": ["NapCat.Shell.exe", "NapCatWinBootMain"],
            "hermes_gateway": ["hermes_cli.main", "gateway"],
            "live2d": ["live2d-electron", "electron"],
        }
        pats = patterns.get(key, [])
        for pat in pats:
            try:
                # Fast kill: taskkill by image name (works for .exe) or
                # targeted WMI for python scripts (filtered, faster)
                if pat.endswith(".exe"):
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/IM", pat],
                        capture_output=True, timeout=5,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                else:
                    subprocess.run(
                        ["powershell", "-NoProfile", "-Command",
                         f"Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                         f"Where-Object {{ $_.CommandLine -like '*{pat}*' }} | "
                         f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force }}"],
                        capture_output=True, timeout=8,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
            except Exception:
                pass
        # Also kill by port
        if key in SERVICES:
            port = SERVICES[key]["port"]
            if key == "napcat":
                # Multi-method kill: pattern + port + cmd wrapper
                for pattern in ('*NapCat.Shell*', '*NapCatWinBootMain*'):
                    try:
                        subprocess.run(
                            ["powershell", "-NoProfile", "-Command",
                             "Get-CimInstance Win32_Process | "
                             f"Where-Object {{ $_.CommandLine -like '{pattern}' }} | "
                             "ForEach-Object { taskkill /F /PID $_.ProcessId /T }"],
                            capture_output=True, timeout=8,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                    except Exception:
                        pass
                for port in (3000, 3001):
                    try:
                        subprocess.run(
                            ["powershell", "-NoProfile", "-Command",
                             f"$p = Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | "
                             "Select-Object -ExpandProperty OwningProcess; "
                             "if ($p) { Stop-Process -Id $p -Force }"],
                            capture_output=True, timeout=10,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                    except Exception:
                        pass
            elif key == "live2d":
                # Live2D runs inside Electron — kill electron.exe processes in the project dir
                try:
                    subprocess.run(
                        ["powershell", "-NoProfile", "-Command",
                         "Get-CimInstance Win32_Process -Filter \"Name='electron.exe'\" | "
                         "Where-Object { $_.CommandLine -like '*live2d-electron*' } | "
                         "ForEach-Object { taskkill /F /PID $_.ProcessId /T }"],
                        capture_output=True, timeout=15,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                except Exception:
                    pass
                # Also kill node processes with live2d-electron
                try:
                    subprocess.run(
                        ["powershell", "-NoProfile", "-Command",
                         "Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | "
                         "Where-Object { $_.CommandLine -like '*live2d-electron*' } | "
                         "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                        capture_output=True, timeout=15,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                except Exception:
                    pass
                _kill_process_on_port(port)
            elif key == "local_vision":
                try:
                    subprocess.run(
                        ["powershell", "-NoProfile", "-Command",
                         "Get-CimInstance Win32_Process -Filter \"Name='llama-server.exe'\" | "
                         "ForEach-Object { taskkill /F /PID $_.ProcessId /T }"],
                        capture_output=True, timeout=10,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                except Exception:
                    pass
                _kill_process_on_port(port)
            else:
                _kill_process_on_port(port)

        # Update gateway state file so dashboard doesn't show false "running"
        if key == "hermes_gateway":
            for state_path in (HERMES_HOME / "gateway_state.json",
                               Path.home() / ".hermes" / "gateway_state.json"):
                try:
                    if state_path.exists():
                        state = json.loads(state_path.read_text(encoding="utf-8"))
                        state["gateway_state"] = "stopped"
                        state["exit_reason"] = "killed by dashboard"
                        state_path.write_text(json.dumps(state, indent=2))
                except Exception:
                    pass

    def _stop_one_service(self, key: str) -> Dict:
        svc = SERVICES[key]
        try:
            self._force_kill_service(key)
            _running_processes.pop(key, None)
            return {"service": key, "name": svc["name"], "status": "stopped"}
        except Exception as e:
            return {"service": key, "name": svc["name"], "status": "error", "error": str(e)}


def run_dashboard(port: int = DASHBOARD_PORT):
    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    logger.info(f"Dashboard running at http://127.0.0.1:{port}")

    # Windows: the process has no visible console (CREATe_NO_WINDOW),
    # so KeyboardInterrupt never fires. Register a console handler.
    _running_flag = True
    if sys.platform == "win32":
        import ctypes
        _WIN_HANDLER = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_ulong)
        def _win_handler_cb(event):
            nonlocal _running_flag; _running_flag = False
            return 1
        _win_handler = _WIN_HANDLER(_win_handler_cb)
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_win_handler, True)

    server.socket.settimeout(0.5)
    try:
        while _running_flag:
            try:
                server.handle_request()
            except (socket.timeout, OSError):
                pass
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        logger.info("Dashboard stopped")


if __name__ == "__main__":
    run_dashboard()
