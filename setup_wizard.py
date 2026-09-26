#!/usr/bin/env python3
"""
Cross-Platform Setup Wizard and Physical Probe Doctor for anki-context-miner

Usage:
    python3 setup_wizard.py                  # Interactive setup wizard
    python3 setup_wizard.py --check-only     # Run physical health checks without modifying files
    python3 setup_wizard.py --install-service # Render template and register background service
    python3 setup_wizard.py --auto           # Non-interactive auto-configuration from env vars
"""

import sys
import os
import platform
import shutil
import subprocess
import json
import urllib.request
import urllib.error
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
CONFIG_PATH = REPO_DIR / "config.json"
CONFIG_EXAMPLE_PATH = REPO_DIR / "config.example.json"
TEMPLATES_DIR = REPO_DIR / "templates"

IS_MAC = platform.system() == "Darwin"
IS_WIN = platform.system() == "Windows"


def print_banner():
    print("=" * 64)
    print("   anki-context-miner: Cross-Platform Physical Setup Wizard")
    print(f"   OS: {platform.system()} {platform.release()} ({platform.machine()})")
    print("=" * 64)


# ── 1. Physical Probe Verifiers ──

def probe_python_version() -> bool:
    v = sys.version_info
    ver_str = f"{v.major}.{v.minor}.{v.micro}"
    if v.major >= 3 and v.minor >= 9:
        print(f"[PASS] Python Version: {ver_str} (>= 3.9 supported)")
        return True
    else:
        print(f"[FAIL] Python Version: {ver_str} (Must be >= 3.9)")
        return False


def probe_ffmpeg() -> bool:
    path = shutil.which("ffmpeg")
    if path:
        print(f"[PASS] FFmpeg Executable: {path}")
        return True
    else:
        print("[FAIL] FFmpeg is not found in PATH.")
        if IS_MAC:
            print("       -> Remediation: run 'brew install ffmpeg'")
        elif IS_WIN:
            print("       -> Remediation: run 'winget install Gyan.FFmpeg' or 'choco install ffmpeg'")
        return False


def probe_ankiconnect(host="127.0.0.1", port=8765) -> bool:
    url = f"http://{host}:{port}"
    req_body = json.dumps({"action": "version", "version": 6}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=req_body,
        headers={"Content-Type": "application/json", "Origin": f"http://{host}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("result"):
                print(f"[PASS] AnkiConnect: Online at {url} (API Version: {data.get('result')})")
                return True
    except Exception as e:
        print(f"[FAIL] AnkiConnect: Offline or Unreachable at {url}")
        print(f"       Details: {e}")
        print("       -> Remediation: 1. Start Anki Desktop.")
        print("                       2. In Anki -> Tools -> Add-ons, install AnkiConnect (code: 2055492159).")
        print("                       3. Restart Anki and re-run this check.")
        return False
    return False


def probe_telegram_bot(token: str, proxy: str = None) -> bool:
    if not token or token == "YOUR_TELEGRAM_BOT_TOKEN":
        print("[WARN] Telegram Bot Token: Not configured yet.")
        return False

    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        handlers = []
        if proxy:
            handlers.append(urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
        opener = urllib.request.build_opener(*handlers)
        req = urllib.request.Request(url, headers={"User-Agent": "anki-video-miner-probe"})
        with opener.open(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("ok"):
                bot_user = data.get("result", {}).get("username", "Unknown")
                print(f"[PASS] Telegram Bot Token: Valid (Connected as @{bot_user})")
                return True
    except Exception as e:
        print(f"[FAIL] Telegram Bot Token: Connection failed.")
        print(f"       Details: {e}")
        print("       -> Note: If you require a proxy for Telegram, ensure proxy_url is set.")
        return False
    return False


def probe_llm_backend(config: dict) -> bool:
    llm_cfg = config.get("llm", {})
    provider = llm_cfg.get("provider", "gemini").lower()
    
    # Check CLI availability first if requested
    if provider in ["codex", "agy", "claude"]:
        cli_map = {"codex": "codex", "agy": "agy", "claude": "claude"}
        exe = shutil.which(cli_map[provider])
        if exe:
            print(f"[PASS] Local LLM CLI ({provider}): Found at {exe}")
            return True
        else:
            print(f"[WARN] Local LLM CLI ({provider}): Not found in PATH.")
            return False

    api_key = (
        llm_cfg.get(f"{provider}_api_key")
        or (llm_cfg.get("anthropic_api_key") if provider == "claude" else None)
        or llm_cfg.get("api_key")
        or os.environ.get(f"{provider.upper()}_API_KEY")
    )
    if not api_key or "YOUR_" in api_key:
        print(f"[WARN] LLM Provider ({provider}): API Key not configured.")
        return False

    print(f"[PASS] LLM Provider ({provider}): API Key configured.")
    return True


# ── 2. Config Generator ──

def load_or_init_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    if CONFIG_EXAMPLE_PATH.exists():
        with open(CONFIG_EXAMPLE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    print(f"[PASS] Config written to {CONFIG_PATH}")


# ── 3. Strict Template Interpolation for Background Service ──

def install_macos_service(cfg: dict) -> bool:
    template_file = TEMPLATES_DIR / "com.anki_video_miner.bot.plist.template"
    if not template_file.exists():
        print(f"[FAIL] Missing template: {template_file}")
        return False

    launch_agents = Path.home() / "Library" / "LaunchAgents"
    launch_agents.mkdir(parents=True, exist_ok=True)
    target_plist = launch_agents / "com.anki_video_miner.bot.plist"

    # Resolve python interpreter
    venv_python = REPO_DIR / ".venv" / "bin" / "python3"
    python_path = str(venv_python if venv_python.exists() else sys.executable)
    bot_script = str(REPO_DIR / "tg_bot.py")

    content = template_file.read_text(encoding="utf-8")
    content = content.replace("{{LABEL}}", "com.anki_video_miner.bot")
    content = content.replace("{{PYTHON_PATH}}", python_path)
    content = content.replace("{{BOT_SCRIPT_PATH}}", bot_script)
    content = content.replace("{{WORKING_DIR}}", str(REPO_DIR))
    content = content.replace("{{LOG_PATH}}", str(REPO_DIR / "bot.log"))
    content = content.replace("{{ERR_PATH}}", str(REPO_DIR / "bot_err.log"))
    content = content.replace("{{EXTRA_PATH}}", str(Path(python_path).parent))

    target_plist.write_text(content, encoding="utf-8")
    print(f"[PASS] Rendered plist to {target_plist}")

    # Unload previous and load new
    subprocess.run(["launchctl", "unload", str(target_plist)], stderr=subprocess.DEVNULL)
    ret = subprocess.run(["launchctl", "load", str(target_plist)])
    if ret.returncode == 0:
        print("[PASS] macOS launchd service 'com.anki_video_miner.bot' loaded successfully.")
        return True
    else:
        print("[FAIL] Failed to load launchd service.")
        return False


def install_windows_service(cfg: dict) -> bool:
    # 1. Render start batch script
    bat_template = TEMPLATES_DIR / "anki_video_miner_start.bat.template"
    target_bat = REPO_DIR / "run_bot.bat"
    
    venv_python = REPO_DIR / ".venv" / "Scripts" / "python.exe"
    venv_pythonw = REPO_DIR / ".venv" / "Scripts" / "pythonw.exe"
    venv_activate = REPO_DIR / ".venv" / "Scripts" / "activate.bat"
    
    python_path = str(venv_python if venv_python.exists() else sys.executable)
    pythonw_path = str(venv_pythonw if venv_pythonw.exists() else python_path)
    bot_script = str(REPO_DIR / "tg_bot.py")

    if bat_template.exists():
        bat_content = bat_template.read_text(encoding="utf-8")
        bat_content = bat_content.replace("{{WORKING_DIR}}", str(REPO_DIR))
        bat_content = bat_content.replace("{{VENV_ACTIVATE}}", str(venv_activate))
        bat_content = bat_content.replace("{{PYTHON_PATH}}", python_path)
        bat_content = bat_content.replace("{{BOT_SCRIPT_PATH}}", bot_script)
        bat_content = bat_content.replace("{{ERR_PATH}}", str(REPO_DIR / "bot_err.log"))
        target_bat.write_text(bat_content, encoding="utf-8")
        print(f"[PASS] Rendered Windows start script to {target_bat}")

    # 2. Render invisible VBS runner
    vbs_template = TEMPLATES_DIR / "run_hidden.vbs.template"
    target_vbs = REPO_DIR / "run_bot_hidden.vbs"
    if vbs_template.exists():
        vbs_content = vbs_template.read_text(encoding="utf-8")
        vbs_content = vbs_content.replace("{{WORKING_DIR}}", str(REPO_DIR))
        vbs_content = vbs_content.replace("{{PYTHONW_PATH}}", pythonw_path)
        vbs_content = vbs_content.replace("{{BOT_SCRIPT_PATH}}", bot_script)
        target_vbs.write_text(vbs_content, encoding="utf-8")
        print(f"[PASS] Rendered invisible VBS runner to {target_vbs}")

    # 3. Add to Windows Startup Folder
    appdata = os.environ.get("APPDATA")
    if appdata:
        startup_dir = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        if startup_dir.exists():
            startup_vbs = startup_dir / "anki_video_miner_hidden.vbs"
            shutil.copy2(target_vbs, startup_vbs)
            print(f"[PASS] Installed to Windows Startup Folder: {startup_vbs}")
            print("       -> Will start automatically on user login with zero black terminal popup.")
            return True

    print("[WARN] Could not locate Startup folder. You can run 'run_bot.bat' manually.")
    return True


# ── 4. Main Entrypoint ──

def run_checks(cfg: dict) -> dict:
    results = {}
    results["python"] = probe_python_version()
    results["ffmpeg"] = probe_ffmpeg()
    results["anki"] = probe_ankiconnect()
    
    tg_token = cfg.get("telegram", {}).get("bot_token")
    tg_proxy = cfg.get("telegram", {}).get("proxy_url")
    results["telegram"] = probe_telegram_bot(tg_token, tg_proxy)
    results["llm"] = probe_llm_backend(cfg)
    return results


def main():
    print_banner()
    cfg = load_or_init_config()

    if "--check-only" in sys.argv:
        print("\n--- Physical Health Checks ---")
        res = run_checks(cfg)
        sys.exit(0 if (res["python"] and res["ffmpeg"]) else 1)

    if "--install-service" in sys.argv:
        print("\n--- Background Service Installation ---")
        if IS_MAC:
            success = install_macos_service(cfg)
        elif IS_WIN:
            success = install_windows_service(cfg)
        else:
            print("[INFO] Linux/other: Run 'python3 tg_bot.py' under systemd or supervisor.")
            success = True
        sys.exit(0 if success else 1)

    if "--auto" in sys.argv:
        print("\n--- Non-Interactive Auto Configuration ---")
        # Read from environment variables if present
        if os.environ.get("TELEGRAM_BOT_TOKEN"):
            cfg.setdefault("telegram", {})["bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"]
        if os.environ.get("TELEGRAM_ADMIN_ID"):
            cfg.setdefault("telegram", {})["admin_user_id"] = int(os.environ["TELEGRAM_ADMIN_ID"])
        if os.environ.get("LLM_PROVIDER"):
            cfg.setdefault("llm", {})["provider"] = os.environ["LLM_PROVIDER"]
        save_config(cfg)
        run_checks(cfg)
        sys.exit(0)

    # Interactive Wizard Mode
    print("\n--- Step 1: Physical Environment Diagnosis ---")
    res = run_checks(cfg)

    print("\n--- Step 2: Essential Credentials Configuration ---")
    current_token = cfg.get("telegram", {}).get("bot_token", "")
    print(f"Current Telegram Bot Token: {current_token[:8]}*** if set")
    user_token = input("Enter Telegram Bot Token (press Enter to keep current): ").strip()
    if user_token:
        cfg.setdefault("telegram", {})["bot_token"] = user_token

    current_admin = cfg.get("telegram", {}).get("admin_user_id", "")
    user_admin = input(f"Enter Telegram Admin User ID (Current: {current_admin}): ").strip()
    if user_admin:
        try:
            cfg.setdefault("telegram", {})["admin_user_id"] = int(user_admin)
        except ValueError:
            print("[WARN] Invalid user ID format, skipping.")

    current_provider = cfg.get("llm", {}).get("provider", "gemini")
    print(f"\nAvailable LLM Providers: gemini, deepseek, openai, claude, codex, agy")
    user_provider = input(f"Choose LLM Provider (Current: {current_provider}): ").strip().lower()
    if user_provider:
        cfg.setdefault("llm", {})["provider"] = user_provider

    active_provider = cfg.get("llm", {}).get("provider", "gemini").lower()
    if active_provider not in ["codex", "agy", "claude"]:
        current_key = (
            cfg.get("llm", {}).get("api_key")
            or cfg.get("llm", {}).get(f"{active_provider}_api_key")
            or (cfg.get("llm", {}).get("anthropic_api_key") if active_provider == "claude" else "")
            or ""
        )
        prompt_txt = f"Enter LLM API Key for '{active_provider}' (press Enter to keep current): "
        user_key = input(prompt_txt).strip()
        if user_key:
            cfg.setdefault("llm", {})["api_key"] = user_key
            if active_provider == "gemini":
                cfg["llm"]["gemini_api_key"] = user_key
            elif active_provider in ["openai", "deepseek"]:
                cfg["llm"]["openai_api_key"] = user_key
                if active_provider == "deepseek":
                    cfg["llm"].setdefault("openai_base_url", "https://api.deepseek.com/v1")
                    cfg["llm"].setdefault("openai_model", "deepseek-flash")
            elif active_provider == "claude":
                cfg["llm"]["anthropic_api_key"] = user_key

    save_config(cfg)

    print("\n--- Step 3: Re-checking Connectivity ---")
    run_checks(cfg)

    install_svc = input("\nDo you want to register the auto-start background service now? [Y/n]: ").strip().lower()
    if install_svc in ["", "y", "yes"]:
        if IS_MAC:
            install_macos_service(cfg)
        elif IS_WIN:
            install_windows_service(cfg)


if __name__ == "__main__":
    main()
