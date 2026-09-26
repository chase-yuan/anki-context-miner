#!/usr/bin/env bash
set -e

echo "=========================================================="
echo "    anki-video-miner One-Click Installer (macOS/Linux)    "
echo "=========================================================="

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

# 1. Check Python
if ! command -v python3 &>/dev/null; then
    echo "[ERROR] python3 could not be found. Please install Python 3.9+ first."
    exit 1
fi

# 2. Setup Sandboxed Virtual Environment (.venv)
if [ ! -d ".venv" ]; then
    echo "[INFO] Creating isolated virtual environment in .venv..."
    python3 -m venv .venv
fi

echo "[INFO] Activating virtual environment..."
source .venv/bin/activate

# 3. Install requirements
echo "[INFO] Installing / updating Python dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

# 4. Copy config.example.json if config.json does not exist
if [ ! -f "config.json" ]; then
    echo "[INFO] Initializing config.json from config.example.json..."
    cp config.example.json config.json
fi

# 5. Launch Setup Wizard
python3 setup_wizard.py
