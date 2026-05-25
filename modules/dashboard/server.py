"""
Hermes Dashboard -- QQ Bot management web UI backend.

Endpoints:
  GET  /                          Dashboard HTML
  GET  /api/status                Full system status
  GET  /api/services/status       Service health
  GET  /api/services/logs         Recent logs
  GET  /api/services/logs/stream  SSE log stream
  POST /api/services/start        Start service
  POST /api/services/stop         Stop service
  POST /api/services/restart      Restart service
  POST /api/services/start-all    Start all
  POST /api/services/stop-all     Stop all
  GET  /api/live2d/models         List Live2D characters
  POST /api/live2d/switch         Switch Live2D model
  GET  /api/memory/workflows      Workflow health
  GET  /api/memory/health         Memory stats
  GET  /api/memory/timeline       Growth timeline (7 days)
  POST /api/memory                Trigger maintenance
  POST /api/memory/search         Search memory
  GET  /api/obsidian              Vault stats
  POST /api/obsidian/search       Search knowledge base
  POST /api/voice                 Set voice mode
  POST /api/napcat/start          Start NapCat
  POST /api/napcat/stop           Stop NapCat
"""

from __future__ import annotations
import json, logging, os, socket, subprocess, sys, threading, time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

# -- paths --
ROOT = Path(__file__).resolve().parent.parent.parent
HERMES_HOME = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))
STATIC_DIR = Path(__file__).resolve().parent / "static"

# -- logging --
logger = logging.getLogger("dashboard")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

# -- config --
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8899"))

# ============================================================
# Services
# ============================================================

SERVICES = {
    "napcat": {"name": "NapCat (QQ)", "port": 3000,
               "cwd": str(ROOT / "napcat"),
               "cmd": ["cmd", "/c", "napcat.bat"],
               "check": lambda: True,
               "color": "#34C759"},
    "hermes_gateway": {"name": "Gateway", "port": 18789,
                       "cwd": str(ROOT),
                       "cmd": [str(ROOT / ".venv" / "Scripts" / "python.exe"),
                               "-m", "hermes_cli.main", "gateway"],
                       "check": lambda: True,
                       "color": "#007AFF"},
    "live2d": {"name": "Live2D", "port": 19920,
               "cwd": str(ROOT / "modules" / "live2d"),
               "cmd": ["cmd", "/c", "npx", "electron", "."],
               "check": lambda: (Path(ROOT / "modules" / "live2d" / "node_modules" / ".package-lock.json")).exists()
                              or (Path(ROOT / "modules" / "live2d" / "node_modules" / "electron")).exists(),
               "color": "#AF52DE"},
    "gptsovits": {"name": "GPT-SoVITS", "port": 9880,
                  "cwd": str(ROOT / "modules" / "tts"),
                  "cmd": ["python", "api_v2.py"],
                  "check": lambda: False,  # User must install separately
                  "color": "#FF9500"},
    "tts_adapter": {"name": "TTS Adapter", "port": 5000,
                    "cwd": str(ROOT / "modules" / "tts"),
                    "cmd": ["python", "ts_adapter.py"],
                    "check": lambda: (Path(ROOT / "modules" / "tts" / "ts_adapter.py")).exists(),
                    "color": "#FF6B35"},
}

_running: Dict[str, subprocess.Popen] = {}
_logs: Dict[str, list] = {s: [] for s in SERVICES}
_log_lock = threading.Lock()
_log_streams: list = []  # SSE clients for live log

# ============================================================
# Process helpers
# ============================================================

def _kill_by_patterns(patterns: list, timeout: int = 10):
    """Kill processes whose command line matches any pattern."""
    for pat in patterns:
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_Process | "
                 f"Where-Object {{ $_.CommandLine -like '*{pat}*' }} | "
                 "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                capture_output=True, timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception:
            pass

def _kill_by_port(port: int):
    """Kill process listening on a port."""
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"$p = Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | "
             "Select-Object -ExpandProperty OwningProcess -Unique; "
             "if ($p) { Stop-Process -Id $p -Force }"],
            capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        pass

KILL_PATTERNS = {
    "napcat": ["napcat.mjs", "NapCat"],
    "hermes_gateway": ["hermes_cli.main", "gateway"],
    "live2d": ["electron", "modules\\live2d"],
    "gptsovits": ["api_v2.py"],
    "tts_adapter": ["ts_adapter.py"],
}

# ============================================================
# Service management
# ============================================================

def _start_service(key: str) -> Dict:
    svc = SERVICES[key]
    if not svc["check"]():
        return {"service": key, "name": svc["name"], "status": "unavailable",
                "msg": f"{svc['name']} not installed - see modules/tts/README.md"}

    # Kill old instance first
    _kill_by_patterns(KILL_PATTERNS.get(key, []))
    _kill_by_port(svc["port"])

    try:
        proc = subprocess.Popen(
            svc["cmd"], cwd=svc["cwd"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW)
        _running[key] = proc

        # Start log reader
        def _read():
            for line in proc.stdout:
                line = line.rstrip()
                if not line:
                    continue
                ts = time.strftime("%H:%M:%S")
                with _log_lock:
                    _logs[key].append({"ts": ts, "service": key, "line": line})
                    if len(_logs[key]) > 200:
                        _logs[key] = _logs[key][-200:]
                # Notify SSE clients
                for q in _log_streams:
                    q.append({"ts": ts, "service": key, "line": line})
        threading.Thread(target=_read, daemon=True).start()
        return {"service": key, "name": svc["name"], "status": "running", "pid": proc.pid}
    except FileNotFoundError:
        return {"service": key, "name": svc["name"], "status": "error",
                "msg": f"Command not found: {svc['cmd'][0]}"}
    except Exception as e:
        return {"service": key, "name": svc["name"], "status": "error", "msg": str(e)}

def _stop_service(key: str) -> Dict:
    svc = SERVICES[key]
    _kill_by_patterns(KILL_PATTERNS.get(key, []))
    _kill_by_port(svc["port"])
    proc = _running.pop(key, None)
    if proc:
        try:
            proc.terminate()
        except Exception:
            pass
    return {"service": key, "name": svc["name"], "status": "stopped"}

def _is_service_running(key: str) -> bool:
    # Check managed process
    proc = _running.get(key)
    if proc and proc.poll() is None:
        return True
    # Check port
    svc = SERVICES[key]
    try:
        s = socket.socket()
        s.settimeout(0.5)
        result = s.connect_ex(("127.0.0.1", svc["port"])) == 0
        s.close()
        return result
    except Exception:
        return False

# ============================================================
# Memory helpers
# ============================================================

def _get_memory_store():
    db_path = HERMES_HOME / "memory_store.db"
    if not db_path.exists():
        return None
    return sqlite3.connect(str(db_path))

def _build_memory_timeline():
    db = _get_memory_store()
    if not db:
        return []
    days = []
    for i in range(6, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        ts = int(datetime.strptime(d, "%Y-%m-%d").timestamp())
        row = db.execute(
            "SELECT COUNT(*) FROM memory_store WHERE created_at >= ? AND created_at < ?",
            (ts, ts + 86400)).fetchone()
        days.append({"date": d, "count": row[0] if row else 0})
    return days

def _get_memory_stats():
    db = _get_memory_store()
    if not db:
        return {"available": False}
    total = db.execute("SELECT COUNT(*) FROM memory_store").fetchone()[0]
    facts = db.execute("SELECT COUNT(*) FROM memory_store WHERE type='fact'").fetchone()[0] if _column_exists(db, "memory_store", "type") else 0
    size_mb = round(os.path.getsize(HERMES_HOME / "memory_store.db") / 1048576, 2)
    return {"available": True, "total": total, "facts": facts, "size_mb": size_mb}

def _column_exists(db, table, column):
    try:
        db.execute(f"SELECT {column} FROM {table} LIMIT 1")
        return True
    except Exception:
        return False

def _search_memory(query: str, limit: int = 20) -> list:
    db = _get_memory_store()
    if not db:
        return []
    try:
        rows = db.execute(
            "SELECT key, value, confidence, created_at FROM memory_store "
            "WHERE key LIKE ? OR value LIKE ? ORDER BY created_at DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", limit)).fetchall()
    except Exception:
        return []
    return [{"key": r[0], "value": (r[1] or "")[:300], "confidence": r[2],
             "created_at": r[3]} for r in rows]

def _obsidian_stats():
    """Get obsidian vault stats from gateway memory."""
    try:
        from agent.memory.gateway import UnifiedMemoryGateway
        gw = UnifiedMemoryGateway.get_instance()
        if gw and gw._obsidian_vault:
            vault = gw._obsidian_vault
            return {"available": True, "notes": len(vault.notes), "links": sum(len(n.links) for n in vault.notes.values()),
                    "backlinks": sum(len(n.backlinks) for n in vault.notes.values()), "tags": len(set(t for n in vault.notes.values() for t in n.tags)),
                    "moc_count": sum(1 for n in vault.notes.values() if n.is_moc)}
    except Exception:
        pass
    return {"available": False}

def _obsidian_search(query: str, top_k: int = 10):
    try:
        from agent.memory.gateway import UnifiedMemoryGateway
        gw = UnifiedMemoryGateway.get_instance()
        if gw and gw._obsidian_vault:
            results = gw._obsidian_vault.search(query, top_k)
            return [{"title": r.title, "snippet": r.snippet[:300], "score": round(r.score, 3)} for r in results]
    except Exception:
        pass
    return []

def _get_workflow_health():
    db = _get_memory_store()
    if not db:
        return []
    try:
        rows = db.execute(
            "SELECT name, last_used, use_count, decay_factor FROM workflows ORDER BY last_used DESC LIMIT 50"
        ).fetchall()
    except Exception:
        return []
    now = time.time()
    return [{"name": r[0], "last_used": r[1], "use_count": r[2],
             "decay": round(r[3], 3) if r[3] else 0,
             "hours_since": round((now - r[1]) / 3600, 1) if r[1] else None} for r in rows]

# ============================================================
# HTTP Server
# ============================================================

class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # quiet

    def _send(self, status: int, body: str = "", content_type: str = "application/json"):
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.wfile.write(body)

    def _send_json(self, data):
        self._send(200, json.dumps(data, ensure_ascii=False, default=str))

    def _send_html(self, html: str):
        self._send(200, html, "text/html")

    def _read_body(self) -> Dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length))

    # -- routing --
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            return self._serve_index()
        if path == "/api/status":
            return self._handle_status()
        if path == "/api/services/status":
            return self._handle_services_status()
        if path == "/api/services/logs":
            return self._handle_logs()
        if path == "/api/services/logs/stream":
            return self._handle_log_stream()
        if path == "/api/live2d/models":
            return self._handle_live2d_models()
        if path == "/api/memory/workflows":
            return self._handle_workflows()
        if path == "/api/memory/health":
            return self._handle_memory_health()
        if path == "/api/memory/timeline":
            return self._handle_memory_timeline()
        if path == "/api/obsidian":
            return self._handle_obsidian()
        self._send_json({"error": "not found", "path": path})

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_body()
        if path == "/api/voice":
            return self._handle_voice(body)
        if path == "/api/services/start":
            return self._handle_start(body)
        if path == "/api/services/stop":
            return self._handle_stop(body)
        if path == "/api/services/restart":
            return self._handle_restart(body)
        if path == "/api/services/start-all":
            return self._handle_start_all()
        if path == "/api/services/stop-all":
            return self._handle_stop_all()
        if path == "/api/memory":
            return self._handle_memory_maint()
        if path == "/api/memory/search":
            return self._handle_memory_search(body)
        if path == "/api/obsidian/search":
            return self._handle_obsidian_search(body)
        if path == "/api/live2d/switch":
            return self._handle_live2d_switch(body)
        if path == "/api/napcat/start":
            return self._handle_napcat_start()
        if path == "/api/napcat/stop":
            return self._handle_napcat_stop()
        self._send_json({"error": "not found"})

    # -- handlers --
    def _serve_index(self):
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        self._send_html(html)

    def _handle_status(self):
        mem = _get_memory_stats()
        ws = _get_workflow_health()
        gw_running = _is_service_running("hermes_gateway")
        nap_running = _is_service_running("napcat")
        self._send_json({
            "gateway": {"running": gw_running},
            "onebot": {"connected": nap_running},
            "memory": mem,
            "workflows": ws[:20],
            "uptime": round(time.time() - _start_time) if "_start_time" in dir() else 0,
        })

    def _handle_services_status(self):
        services = {}
        for key, svc in SERVICES.items():
            services[key] = {
                "name": svc["name"], "port": svc["port"], "color": svc["color"],
                "running": _is_service_running(key),
                "available": svc["check"](),
            }
        self._send_json({"services": services})

    def _handle_logs(self):
        svc = urlparse(self.path).query.split("=")[-1] if "=" in urlparse(self.path).query else "hermes_gateway"
        tail = int(urlparse(self.path).query.split("tail=")[-1].split("&")[0]) if "tail=" in urlparse(self.path).query else 50
        with _log_lock:
            lines = _logs.get(svc, [])[-tail:]
        self._send_json({"service": svc, "lines": lines})

    def _handle_log_stream(self):
        """SSE log stream."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        queue = []
        _log_streams.append(queue)
        try:
            while True:
                if queue:
                    data = json.dumps(queue.pop(0), ensure_ascii=False)
                    self.wfile.write(f"data: {data}\n\n".encode())
                    self.wfile.flush()
                else:
                    time.sleep(0.5)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            _log_streams.remove(queue)

    def _handle_start(self, body):
        key = body.get("service", "")
        if key not in SERVICES:
            return self._send_json({"error": f"unknown service: {key}"})
        self._send_json(_start_service(key))

    def _handle_stop(self, body):
        key = body.get("service", "")
        if key not in SERVICES:
            return self._send_json({"error": f"unknown service: {key}"})
        self._send_json(_stop_service(key))

    def _handle_restart(self, body):
        key = body.get("service", "")
        if key not in SERVICES:
            return self._send_json({"error": f"unknown service: {key}"})
        _stop_service(key)
        time.sleep(1)
        self._send_json(_start_service(key))

    def _handle_start_all(self):
        results = []
        for key in SERVICES:
            if SERVICES[key]["check"]():
                results.append(_start_service(key))
            else:
                results.append({"service": key, "name": SERVICES[key]["name"], "status": "unavailable"})
        self._send_json({"results": results})

    def _handle_stop_all(self):
        results = [_stop_service(key) for key in SERVICES]
        self._send_json({"results": results})

    def _handle_live2d_models(self):
        l2d = ROOT / "modules" / "live2d" / "assets" / "figure"
        models = {}
        if l2d.exists():
            for char_dir in sorted(l2d.iterdir()):
                if not char_dir.is_dir():
                    continue
                outfits = [o.name for o in sorted(char_dir.iterdir())
                           if o.is_dir() and not o.name.startswith(".") and (o / "model.json").exists()]
                if outfits:
                    models[char_dir.name] = {"name": char_dir.name, "outfits": outfits}
        self._send_json({"characters": models})

    def _handle_live2d_switch(self, body):
        char = body.get("character", "")
        outfit = body.get("outfit", "")
        if not char:
            return self._send_json({"error": "character required"})
        # Save preference
        pref_file = HERMES_HOME / "live2d_pref.json"
        pref_file.write_text(json.dumps({"character": char, "outfit": outfit}), encoding="utf-8")
        self._send_json({"status": "ok", "character": char, "outfit": outfit})

    def _handle_workflows(self):
        self._send_json(_get_workflow_health())

    def _handle_memory_health(self):
        mem = _get_memory_stats()
        timeline = _build_memory_timeline()
        self._send_json({"memory": mem, "timeline": timeline})

    def _handle_memory_timeline(self):
        self._send_json(_build_memory_timeline())

    def _handle_memory_maint(self):
        try:
            from agent.memory.gateway import UnifiedMemoryGateway
            gw = UnifiedMemoryGateway.get_instance()
            if gw:
                gw.consolidate_stm()
                gw.decay_ltm()
                self._send_json({"status": "ok"})
            else:
                self._send_json({"status": "unavailable"})
        except Exception as e:
            self._send_json({"status": "error", "msg": str(e)})

    def _handle_memory_search(self, body):
        q = body.get("query", "")
        if not q:
            return self._send_json({"results": []})
        self._send_json({"results": _search_memory(q)})

    def _handle_obsidian(self):
        self._send_json(_obsidian_stats())

    def _handle_obsidian_search(self, body):
        q = body.get("query", "")
        if not q:
            return self._send_json({"results": []})
        self._send_json({"results": _obsidian_search(q)})

    def _handle_voice(self, body):
        chat_id = body.get("chat_id", "")
        mode = body.get("mode", "off")
        if not chat_id:
            return self._send_json({"error": "chat_id required"})
        voice_file = HERMES_HOME / "voice_modes.json"
        modes = {}
        if voice_file.exists():
            modes = json.loads(voice_file.read_text(encoding="utf-8"))
        modes[chat_id] = mode
        voice_file.write_text(json.dumps(modes, ensure_ascii=False), encoding="utf-8")
        self._send_json({"status": "ok", "chat_id": chat_id, "mode": mode})

    def _handle_napcat_start(self):
        _start_service("napcat")
        self._send_json({"status": "starting"})

    def _handle_napcat_stop(self):
        _stop_service("napcat")
        self._send_json({"status": "stopped"})


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

# ============================================================
# Main
# ============================================================

def run(port: int = DASHBOARD_PORT):
    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    logger.info(f"Dashboard running at http://127.0.0.1:{port}")
    server.socket.settimeout(0.5)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run()
