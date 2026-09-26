@echo off
chcp 65001 >nul
echo ==========================================================
echo      anki-context-miner One-Click Installer (Windows)
echo ==========================================================

cd /d "%~dp0"

:: 1. Check Python
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] python is not found in PATH.
    echo Please install Python 3.10+ from python.org and check "Add Python to PATH".
    pause
    exit /b 1
)

:: 2. Setup Sandboxed Virtual Environment (.venv)
if not exist ".venv" (
    echo [INFO] Creating isolated virtual environment in .venv...
    python -m venv .venv
)

echo [INFO] Activating virtual environment...
call .venv\Scripts\activate.bat

:: 3. Install requirements
echo [INFO] Installing / updating Python dependencies...
python -m pip install --upgrade pip -q
pip install -r requirements.txt -q

:: 4. Copy config.example.json if config.json does not exist
if not exist "config.json" (
    echo [INFO] Initializing config.json from config.example.json...
    copy config.example.json config.json >nul
)

:: 5. Launch Setup Wizard
python setup_wizard.py

pause
