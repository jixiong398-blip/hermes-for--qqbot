#!/bin/bash
cd "$(dirname "$0")"
while true; do
    clear
    echo "  ╔══════════════════════════════════╗"
    echo "  ║     QQBot Control Center         ║"
    echo "  ╠══════════════════════════════════╣"
    echo "  ║  1. Start All Services           ║"
    echo "  ║  2. Start NapCat (QQ)            ║"
    echo "  ║  3. Start Gateway+Dashboard      ║"
    echo "  ║  4. Stop All Services            ║"
    echo "  ║  5. Open Dashboard               ║"
    echo "  ║  0. Exit                         ║"
    echo "  ╚══════════════════════════════════╝"
    echo ""
    read -p "  Choice: " choice
    case $choice in
        1) [ -f "napcat/launcher.sh" ] && (cd napcat && bash launcher.sh &); source .venv/bin/activate 2>/dev/null; hermes gateway &; .venv/bin/python modules/dashboard/server.py &;;
        2) [ -f "napcat/launcher.sh" ] && (cd napcat && bash launcher.sh &);;
        3) source .venv/bin/activate 2>/dev/null; hermes gateway &; .venv/bin/python modules/dashboard/server.py &;;
        4) pkill -f "hermes gateway"; pkill -f "server.py"; pkill -f "napcat";;
        5) xdg-open http://127.0.0.1:8899 2>/dev/null || open http://127.0.0.1:8899 2>/dev/null;;
        0) exit 0;;
    esac
    read -p "  Press Enter..."
done
