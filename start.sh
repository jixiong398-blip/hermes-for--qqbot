#!/bin/bash
cd "$(dirname "$0")"

if [ ! -f ".venv/bin/python" ]; then
    echo "[ERROR] venv not found - run install.sh first"
    exit 1
fi

echo "  ◆ QQBot"
echo "  ========="

echo "  [NapCat] Launching..."
[ -f "napcat/launcher.sh" ] && (cd napcat && bash launcher.sh &)

echo "  [Gateway] Launching..."
.venv/bin/python -m hermes_cli.main gateway &

echo "  [Dashboard] Launching..."
.venv/bin/python modules/dashboard/server.py &

sleep 3
xdg-open http://127.0.0.1:8899 2>/dev/null || open http://127.0.0.1:8899 2>/dev/null

echo "  Dashboard: http://127.0.0.1:8899"
echo "  Press Enter to stop..."
read

pkill -f "hermes_cli.main gateway" 2>/dev/null
pkill -f "dashboard/server.py" 2>/dev/null
pkill -f "napcat" 2>/dev/null
echo "  Stopped."
