#!/usr/bin/env python3
"""
Diagnostic Environment Checker (Doctor) for Anki Video Miner.
Validates dependencies, network connectivity, AnkiConnect, and configuration before launch.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

# ANSI colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"


def print_status(item: str, status: bool, detail: str = "", fix: str = ""):
    badge = f"{GREEN}[PASS]{RESET}" if status else f"{RED}[FAIL]{RESET}"
    print(f" {badge} {item:<28} {detail}")
    if not status and fix:
        print(f"        {YELLOW}-> Fix: {fix}{RESET}")


def check_python() -> bool:
    v = sys.version_info
    ok = v.major == 3 and v.minor >= 9
    print_status("Python Runtime", ok, f"v{v.major}.{v.minor}.{v.micro}", "Upgrade to Python 3.9+")
    return ok


def check_binary(name: str, hint: str) -> bool:
    path = shutil.which(name)
    if not path:
        # Check macOS user pip bin
        user_bin = Path.home() / "Library" / "Python" / f"3.{sys.version_info.minor}" / "bin" / name
        if user_bin.is_file() and os.access(user_bin, os.X_OK):
            path = str(user_bin)
        elif name == "yt-dlp":
            try:
                import yt_dlp
                path = f"{sys.executable} -m yt_dlp"
            except ImportError:
                pass
    ok = path is not None
    detail = path if ok else "Not found in PATH"
    print_status(f"CLI: {name}", ok, detail, hint)
    return ok


def check_pip_package(module_name: str, package_name: str) -> bool:
    try:
        __import__(module_name)
        print_status(f"Package: {package_name}", True, "Installed")
        return True
    except ImportError:
        print_status(f"Package: {package_name}", False, "Missing", f"pip install {package_name}")
        return False


def check_config() -> tuple[bool, dict]:
    cfg_path = Path(__file__).resolve().parent / "config.json"
    if not cfg_path.is_file():
        alt = Path.home() / ".config" / "anki-video-miner" / "config.json"
        if alt.is_file():
            cfg_path = alt

    if not cfg_path.is_file():
        print_status("Configuration File", False, "config.json not found", "cp config.example.json config.json")
        return False, {}

    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        token = data.get("telegram", {}).get("bot_token", "")
        has_token = bool(token and not token.startswith("YOUR_"))
        print_status("Configuration File", True, f"Found at {cfg_path.name}")
        print_status("Telegram Bot Token", has_token, f"{token[:8]}..." if has_token else "Empty or placeholder", "Edit config.json and fill in your BotFather token")
        return True, data
    except Exception as e:
        print_status("Configuration File", False, f"JSON parse error: {e}", "Ensure config.json is valid JSON")
        return False, {}


def check_telegram_api(token: str, proxy: str = "") -> bool:
    if not token or token.startswith("YOUR_"):
        return False
    url = f"https://api.telegram.org/bot{token}/getMe"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AnkiVideoMinerDoctor/1.0"})
        opener = urllib.request.build_opener()
        if proxy:
            opener.add_handler(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        with opener.open(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("ok"):
                username = data["result"].get("username", "")
                print_status("Telegram API Connection", True, f"Connected (@{username})")
                return True
    except Exception as e:
        print_status("Telegram API Connection", False, f"Connection failed: {e}", "Verify internet / proxy_url in config.json")
    return False


def check_anki_connect(url: str = "http://127.0.0.1:8765") -> bool:
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps({"action": "version", "version": 6}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=2.0) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            if res.get("result"):
                print_status("AnkiConnect Service", True, f"Connected (v{res['result']}) at {url}")
                return True
    except Exception:
        pass
    print_status(
        "AnkiConnect Service",
        False,
        f"Unable to reach {url}",
        "Ensure Anki desktop is open and AnkiConnect add-on (2055492159) is installed"
    )
    return False


def check_llm_readiness(cfg: dict) -> bool:
    from llm_client import find_executable
    found_engines = []

    # 1. REST APIs
    universal_key = cfg.get("llm", {}).get("api_key")
    gemini_key = cfg.get("llm", {}).get("gemini_api_key") or universal_key or os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        found_engines.append(("Gemini REST API", f"Key: {gemini_key[:6]}..."))

    openai_key = (
        cfg.get("llm", {}).get("openai_api_key")
        or universal_key
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
    )
    if openai_key:
        base = cfg.get("llm", {}).get("openai_base_url") or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1"
        found_engines.append(("OpenAI/DeepSeek API", f"Endpoint: {base}"))

    claude_key = (
        cfg.get("llm", {}).get("anthropic_api_key")
        or universal_key
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("CLAUDE_API_KEY")
    )
    if claude_key:
        found_engines.append(("Claude REST API", f"Key: {claude_key[:6]}..."))

    # 2. Local CLIs
    cli_cfg = cfg.get("llm", {}).get("cli_paths", {})
    codex = find_executable("codex", cli_cfg.get("codex"))
    if codex:
        found_engines.append(("Codex CLI", f"Available at {codex}"))

    agy = find_executable("agy", cli_cfg.get("agy"))
    if agy:
        found_engines.append(("Antigravity CLI", f"Available at {agy}"))

    claude_cli = find_executable("claude", cli_cfg.get("claude"))
    if claude_cli:
        found_engines.append(("Claude Code CLI", f"Available at {claude_cli}"))

    if found_engines:
        for name, detail in found_engines:
            print_status(f"LLM: {name}", True, detail)
        return True

    print_status(
        "LLM Engine",
        False,
        "No API key or CLI detected",
        "Configure GEMINI_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY or install codex / agy / claude"
    )
    return False


def main():
    print(f"\n{BLUE}======================================================{RESET}")
    print(f"{BLUE}      Anki Video Miner - Environment Doctor           {RESET}")
    print(f"{BLUE}======================================================{RESET}\n")

    results = []
    results.append(check_python())
    results.append(check_binary("ffmpeg", "brew install ffmpeg (macOS) or apt install ffmpeg (Ubuntu)"))
    results.append(check_binary("yt-dlp", "brew install yt-dlp or pip install yt-dlp"))

    results.append(check_pip_package("telegram", "python-telegram-bot"))
    results.append(check_pip_package("edge_tts", "edge-tts"))
    results.append(check_pip_package("youtube_transcript_api", "youtube-transcript-api"))

    cfg_ok, cfg = check_config()
    results.append(cfg_ok)

    tg_token = cfg.get("telegram", {}).get("bot_token", "") if cfg_ok else ""
    tg_proxy = cfg.get("telegram", {}).get("proxy_url", "") if cfg_ok else ""
    if tg_token:
        results.append(check_telegram_api(tg_token, tg_proxy))

    anki_url = cfg.get("anki", {}).get("connect_url", "http://127.0.0.1:8765") if cfg_ok else "http://127.0.0.1:8765"
    results.append(check_anki_connect(anki_url))

    if cfg_ok:
        results.append(check_llm_readiness(cfg))

    all_pass = all(results)
    print(f"\n{BLUE}------------------------------------------------------{RESET}")
    if all_pass:
        print(f"{GREEN}[SUCCESS] All checks passed! System is ready to run:{RESET}")
        print("  python3 tg_bot.py\n")
    else:
        print(f"{YELLOW}[ACTION REQUIRED] Please resolve the issues marked [FAIL] above.{RESET}\n")

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
