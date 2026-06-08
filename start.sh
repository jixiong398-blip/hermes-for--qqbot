#!/usr/bin/env bash
cd "$(dirname "$0")"

if [ ! -f ".venv/bin/python" ]; then
    echo "[ERROR] venv not found - run install.sh first"
    exit 1
fi

echo ""
echo "  QQBot"
echo "  ====="
echo ""

# Clean up old processes
echo "  [Clean] Stopping old services..."
pkill -f "hermes_cli.main gateway" 2>/dev/null || killall -q "python" 2>/dev/null || true
pkill -f "dashboard/server.py" 2>/dev/null || true
pkill -f "napcat" 2>/dev/null || true
sleep 2

echo "  [NapCat] Launching..."
if [ -f "napcat/launcher.sh" ]; then
    (cd napcat && bash launcher.sh) &
    echo "  [NapCat] Scan QR code to login"
else
    echo "  [NapCat] NOT FOUND - skipping"
fi

echo "  [Gateway] Launching..."
.venv/bin/python -m hermes_cli.main gateway &

echo "  [Dashboard] Launching..."
.venv/bin/python modules/dashboard/server.py &

sleep 3
xdg-open http://127.0.0.1:8899 2>/dev/null || open http://127.0.0.1:8899 2>/dev/null

echo ""
echo "  Dashboard: http://127.0.0.1:8899"
echo "  Press Enter to stop all services..."
read

echo "  Stopping..."
pkill -f "hermes_cli.main gateway" 2>/dev/null || killall -q "python" 2>/dev/null
pkill -f "dashboard/server.py" 2>/dev/null
pkill -f "napcat" 2>/dev/null
echo "  Stopped."
