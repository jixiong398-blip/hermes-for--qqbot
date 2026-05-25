#!/usr/bin/env bash
cd "$(dirname "$0")"

echo ""
echo "  QQBot Installer"
echo "  ================"
echo ""

echo "  [1/4] Creating venv..."
python3 -m venv .venv 2>/dev/null || python -m venv .venv 2>/dev/null || {
    echo "  [ERROR] Python not found. Install from https://python.org"
    exit 1
}

echo "  [2/4] Installing dependencies..."
source .venv/bin/activate
pip install -e hermes/ -q || {
    echo "  [ERROR] pip install failed"
    exit 1
}

echo "  [3/4] Running setup..."
python3 scripts/install.py || {
    echo "  [ERROR] setup failed"
    exit 1
}

echo "  [4/4] Done."
echo ""
echo "  Next steps:"
echo "    1. Run: python3 scripts/setup_config.py  (select LLM provider)"
echo "    2. Run: bash start.sh"
echo ""
