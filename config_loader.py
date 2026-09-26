#!/usr/bin/env python3
"""
Centralized Configuration Loader for Anki Video Miner.
Resolves configuration from config.json, environment variables, or default fallbacks.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

CONFIG_LOCATIONS = [
    Path(__file__).resolve().parent / "config.json",
    Path.home() / ".config" / "anki-video-miner" / "config.json",
]


def load_config() -> Dict[str, Any]:
    config_data: Dict[str, Any] = {}

    for path in CONFIG_LOCATIONS:
        if path.is_file():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    config_data = json.load(f)
                    break
            except Exception as e:
                print(f"[WARN] Failed to parse config at {path}: {e}", file=sys.stderr)

    # Environment variable overrides
    tg = config_data.setdefault("telegram", {})
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        tg["bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"]
    if os.environ.get("TELEGRAM_ADMIN_ID"):
        try:
            tg["admin_user_id"] = int(os.environ["TELEGRAM_ADMIN_ID"])
        except ValueError:
            pass
    if os.environ.get("HTTP_PROXY") and not tg.get("proxy_url"):
        tg["proxy_url"] = os.environ["HTTP_PROXY"]

    llm = config_data.setdefault("llm", {})
    if os.environ.get("LLM_PROVIDER"):
        llm["provider"] = os.environ["LLM_PROVIDER"]
    if os.environ.get("GEMINI_API_KEY"):
        llm["gemini_api_key"] = os.environ["GEMINI_API_KEY"]
    if os.environ.get("OPENAI_API_KEY"):
        llm["openai_api_key"] = os.environ["OPENAI_API_KEY"]
    if os.environ.get("DEEPSEEK_API_KEY"):
        llm["openai_api_key"] = os.environ["DEEPSEEK_API_KEY"]
        if not llm.get("openai_base_url"):
            llm["openai_base_url"] = "https://api.deepseek.com/v1"
        if not llm.get("openai_model"):
            llm["openai_model"] = "deepseek-chat"
    if os.environ.get("OPENAI_BASE_URL"):
        llm["openai_base_url"] = os.environ["OPENAI_BASE_URL"]
    if os.environ.get("OPENAI_MODEL"):
        llm["openai_model"] = os.environ["OPENAI_MODEL"]
    if os.environ.get("ANTHROPIC_API_KEY"):
        llm["anthropic_api_key"] = os.environ["ANTHROPIC_API_KEY"]
    elif os.environ.get("CLAUDE_API_KEY"):
        llm["anthropic_api_key"] = os.environ["CLAUDE_API_KEY"]
    if os.environ.get("ANTHROPIC_MODEL"):
        llm["anthropic_model"] = os.environ["ANTHROPIC_MODEL"]

    # Map universal api_key to provider-specific key if not already defined
    universal_key = llm.get("api_key")
    if universal_key:
        provider = llm.get("provider", "gemini").lower()
        if provider == "gemini" and not llm.get("gemini_api_key"):
            llm["gemini_api_key"] = universal_key
        elif provider in ["openai", "deepseek"] and not llm.get("openai_api_key"):
            llm["openai_api_key"] = universal_key
            if provider == "deepseek" and not llm.get("openai_base_url"):
                llm["openai_base_url"] = "https://api.deepseek.com/v1"
                llm.setdefault("openai_model", "deepseek-chat")
        elif provider == "claude" and not llm.get("anthropic_api_key"):
            llm["anthropic_api_key"] = universal_key

    anki = config_data.setdefault("anki", {})
    if os.environ.get("ANKI_CONNECT_URL"):
        anki["connect_url"] = os.environ["ANKI_CONNECT_URL"]
    if os.environ.get("ANKI_VOICE"):
        anki["voice"] = os.environ["ANKI_VOICE"]

    # Defaults
    anki.setdefault("connect_url", "http://127.0.0.1:8765")
    anki.setdefault("deck_prefix", "YouTube")
    anki.setdefault("model_name", "Cloze")
    anki.setdefault("voice", "en-US-ChristopherNeural")
    anki.setdefault("auto_sync", True)

    paths = config_data.setdefault("paths", {})
    paths.setdefault("staging_file", str(Path.home() / ".config" / "anki_video_staging.json"))
    paths.setdefault("known_words_file", str(Path.home() / ".config" / "english_known_words.txt"))
    paths.setdefault("obsidian_vault", "")

    return config_data


_CONFIG_CACHE: Optional[Dict[str, Any]] = None


def get_config(reload: bool = False) -> Dict[str, Any]:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None or reload:
        _CONFIG_CACHE = load_config()
    return _CONFIG_CACHE
