#!/bin/bash
cd "$(dirname "$0")"
echo "  ◆ QQBot Installer"
echo "  =================="
echo "  [1/4] Creating venv..."
python3 -m venv .venv || { echo "Python not found"; exit 1; }
echo "  [2/4] Installing dependencies..."
source .venv/bin/activate
pip install -e hermes/ -q
echo "  [3/4] Running setup..."
python3 scripts/install.py
echo "  [4/4] Done!"
echo "  1. Edit SOUL.md (character)"
echo "  2. Edit config.yaml (API key)"
echo "  3. Run: bash start.sh"
