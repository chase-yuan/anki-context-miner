#!/usr/bin/env python3
"""
Multi-Engine LLM Client for Anki Video Miner.
Supports:
1. REST APIs (Zero local dependencies):
   - Google Gemini REST API (gemini-2.5-flash)
   - OpenAI & OpenAI-Compatible REST APIs (DeepSeek, OpenRouter, Ollama, Moonshot, etc.)
   - Anthropic Claude REST API (claude-3-5-haiku, claude-3-5-sonnet)
2. Local Logged-in CLIs (Zero API key needed if logged in locally):
   - OpenAI Codex CLI (`codex exec`)
   - Google Antigravity CLI (`agy -p`)
   - Anthropic Claude Code CLI (`claude -p`)
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("LLMClient")


def find_executable(name: str, custom_path: Optional[str] = None) -> Optional[str]:
    """Locate binary executable across PATH, common homebrew dirs, and custom path."""
    if custom_path and os.path.isfile(custom_path) and os.access(custom_path, os.X_OK):
        return custom_path
    which_path = shutil.which(name)
    if which_path:
        return which_path
    common_mac_paths = [
        f"/opt/homebrew/bin/{name}",
        f"/usr/local/bin/{name}",
        f"{os.path.expanduser('~')}/.local/bin/{name}",
        f"{os.path.expanduser('~')}/Library/Python/3.{sys.version_info.minor}/bin/{name}",
    ]
    for p in common_mac_paths:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


class LLMClient:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        if config is None:
            from config_loader import get_config
            config = get_config()
        self.config = config
        self.llm_cfg = config.get("llm", {})
        self.proxy_url = config.get("telegram", {}).get("proxy_url") or os.environ.get("HTTP_PROXY") or ""

    def _get_opener(self) -> urllib.request.OpenerDirector:
        opener = urllib.request.build_opener()
        if self.proxy_url:
            opener.add_handler(urllib.request.ProxyHandler({
                "http": self.proxy_url,
                "https": self.proxy_url,
            }))
        return opener

    # ─────────────────────────────────────────────────────────────
    # 1. REST API Engines
    # ─────────────────────────────────────────────────────────────

    def call_gemini_api(self, prompt: str, json_mode: bool = False, system_prompt: str = "") -> str:
        """Call official Google Gemini REST API."""
        api_key = self.llm_cfg.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not configured")

        model = self.llm_cfg.get("gemini_model") or "gemini-2.5-flash"
        endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

        body: Dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
            }
        }
        if json_mode:
            body["generationConfig"]["responseMimeType"] = "application/json"
        if system_prompt:
            body["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        req_data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(endpoint, data=req_data, headers={"Content-Type": "application/json"})
        opener = self._get_opener()

        with opener.open(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["candidates"][0]["content"]["parts"][0]["text"]

    def call_openai_api(self, prompt: str, json_mode: bool = False, system_prompt: str = "") -> str:
        """Call OpenAI or OpenAI-compatible endpoint (DeepSeek, OpenRouter, Ollama, etc.)."""
        api_key = (
            self.llm_cfg.get("openai_api_key")
            or os.environ.get("OPENAI_API_KEY")
            or os.environ.get("DEEPSEEK_API_KEY")
        )
        if not api_key:
            raise ValueError("OPENAI_API_KEY or DEEPSEEK_API_KEY not configured")

        base_url = (
            self.llm_cfg.get("openai_base_url")
            or os.environ.get("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        ).rstrip("/")
        endpoint = f"{base_url}/chat/completions"

        model = (
            self.llm_cfg.get("openai_model")
            or os.environ.get("OPENAI_MODEL")
            or ("deepseek-chat" if "deepseek" in base_url.lower() else "gpt-4o-mini")
        )

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        body: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        req_data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=req_data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        opener = self._get_opener()

        with opener.open(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]

    def call_claude_api(self, prompt: str, json_mode: bool = False, system_prompt: str = "") -> str:
        """Call official Anthropic Claude Messages REST API."""
        api_key = (
            self.llm_cfg.get("anthropic_api_key")
            or os.environ.get("ANTHROPIC_API_KEY")
            or os.environ.get("CLAUDE_API_KEY")
        )
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY not configured")

        endpoint = "https://api.anthropic.com/v1/messages"
        model = self.llm_cfg.get("anthropic_model") or "claude-3-5-haiku-20241022"

        body: Dict[str, Any] = {
            "model": model,
            "max_tokens": 4096,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            body["system"] = system_prompt

        req_data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=req_data,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        opener = self._get_opener()

        with opener.open(req, timeout=90) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["content"][0]["text"]

    # ─────────────────────────────────────────────────────────────
    # 2. Local CLI Engines (Zero API Key needed)
    # ─────────────────────────────────────────────────────────────

    def call_codex_cli(self, prompt: str, timeout: int = 120) -> str:
        """Call local OpenAI Codex CLI (`codex exec`)."""
        custom = self.llm_cfg.get("cli_paths", {}).get("codex")
        bin_path = find_executable("codex", custom)
        if not bin_path:
            raise FileNotFoundError("Codex CLI executable not found in PATH or standard directories")

        full_prompt = prompt
        # Use ephemeral non-interactive exec
        cmd = [bin_path, "exec", "--ephemeral", "--color", "never", full_prompt]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if res.returncode != 0:
            err = res.stderr.strip() or res.stdout.strip()
            raise RuntimeError(f"Codex CLI exited with code {res.returncode}: {err[:300]}")
        return res.stdout.strip()

    def call_agy_cli(self, prompt: str, timeout: int = 120) -> str:
        """Call local Google Antigravity CLI (`agy -p`)."""
        custom = self.llm_cfg.get("cli_paths", {}).get("agy")
        bin_path = find_executable("agy", custom)
        if not bin_path:
            raise FileNotFoundError("Antigravity CLI ('agy') not found in PATH or standard directories")

        cmd = [bin_path, "--dangerously-skip-permissions", "-p", prompt]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if res.returncode != 0:
            err = res.stderr.strip() or res.stdout.strip()
            raise RuntimeError(f"Antigravity CLI exited with code {res.returncode}: {err[:300]}")
        return res.stdout.strip()

    def call_claude_cli(self, prompt: str, timeout: int = 120) -> str:
        """Call local Anthropic Claude Code CLI (`claude -p`)."""
        custom = self.llm_cfg.get("cli_paths", {}).get("claude")
        bin_path = find_executable("claude", custom)
        if not bin_path:
            raise FileNotFoundError("Claude Code CLI executable not found in PATH or standard directories")

        cmd = [bin_path, "-p", prompt, "--output-format", "text"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if res.returncode != 0:
            err = res.stderr.strip() or res.stdout.strip()
            raise RuntimeError(f"Claude CLI exited with code {res.returncode}: {err[:300]}")
        return res.stdout.strip()

    # ─────────────────────────────────────────────────────────────
    # 3. Dynamic Dispatcher & Fallback Chain
    # ─────────────────────────────────────────────────────────────

    def dispatch(
        self,
        prompt: str,
        json_mode: bool = False,
        system_prompt: str = "",
        preferred_provider: Optional[str] = None,
        timeout: int = 120,
    ) -> Tuple[str, str]:
        """
        Executes prompt across configured or auto-detected providers.
        Returns: (output_text, engine_used)
        """
        provider = preferred_provider or self.llm_cfg.get("provider", "auto")

        # Specific targeted provider
        if provider and provider != "auto":
            p = provider.lower()
            if p in ["gemini", "gemini_api", "google"]:
                return self.call_gemini_api(prompt, json_mode, system_prompt), "gemini_api"
            elif p in ["openai", "openai_api", "deepseek", "openrouter", "ollama"]:
                return self.call_openai_api(prompt, json_mode, system_prompt), f"{p}_api"
            elif p in ["claude_api", "anthropic", "anthropic_api"]:
                return self.call_claude_api(prompt, json_mode, system_prompt), "claude_api"
            elif p in ["codex", "codex_cli"]:
                return self.call_codex_cli(prompt, timeout), "codex_cli"
            elif p in ["agy", "agy_cli", "antigravity"]:
                return self.call_agy_cli(prompt, timeout), "agy_cli"
            elif p in ["claude", "claude_cli", "claude_code"]:
                return self.call_claude_cli(prompt, timeout), "claude_cli"

        # Auto fallback chain:
        # 1. Configured REST APIs (Fastest & most consistent JSON output)
        # 2. Local logged-in CLIs
        chain: List[Tuple[str, Any]] = []

        # Gemini API
        if self.llm_cfg.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY"):
            chain.append(("gemini_api", lambda: self.call_gemini_api(prompt, json_mode, system_prompt)))

        # OpenAI / DeepSeek API
        if self.llm_cfg.get("openai_api_key") or os.environ.get("OPENAI_API_KEY") or os.environ.get("DEEPSEEK_API_KEY"):
            chain.append(("openai_api", lambda: self.call_openai_api(prompt, json_mode, system_prompt)))

        # Claude API
        if self.llm_cfg.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY"):
            chain.append(("claude_api", lambda: self.call_claude_api(prompt, json_mode, system_prompt)))

        # Local Codex CLI
        if find_executable("codex", self.llm_cfg.get("cli_paths", {}).get("codex")):
            chain.append(("codex_cli", lambda: self.call_codex_cli(prompt, timeout)))

        # Local Antigravity CLI
        if find_executable("agy", self.llm_cfg.get("cli_paths", {}).get("agy")):
            chain.append(("agy_cli", lambda: self.call_agy_cli(prompt, timeout)))

        # Local Claude CLI
        if find_executable("claude", self.llm_cfg.get("cli_paths", {}).get("claude")):
            chain.append(("claude_cli", lambda: self.call_claude_cli(prompt, timeout)))

        if not chain:
            raise RuntimeError(
                "未检测到可用的 LLM 推理引擎！\n"
                "你可以通过以下任一方式启用：\n"
                "1. [API 方式] 在 config.json 或环境变量中配置任一 API Key：\n"
                "   • GEMINI_API_KEY (Google 官方免费 API)\n"
                "   • OPENAI_API_KEY 或 DEEPSEEK_API_KEY (OpenAI / DeepSeek / OpenRouter)\n"
                "   • ANTHROPIC_API_KEY (Anthropic Claude API)\n"
                "2. [CLI 方式] 在本机登录任一 AI 命令行客户端即可零 Key 开箱即用：\n"
                "   • OpenAI Codex (`codex login`)\n"
                "   • Google Antigravity (`agy`)\n"
                "   • Anthropic Claude Code (`claude login`)"
            )

        errors = []
        for name, func in chain:
            try:
                out = func()
                if out and out.strip():
                    return out.strip(), name
            except Exception as e:
                errors.append(f"[{name}] {e}")
                logger.warning(f"Engine {name} failed, falling back to next provider: {e}")

        raise RuntimeError(
            f"所有已检测到的 LLM 引擎均执行失败:\n" + "\n".join(errors) +
            "\n请检查各引擎凭证、配额或网络连接状态。"
        )


_DEFAULT_CLIENT: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _DEFAULT_CLIENT
    if _DEFAULT_CLIENT is None:
        _DEFAULT_CLIENT = LLMClient()
    return _DEFAULT_CLIENT


def call_llm(
    prompt: str,
    json_mode: bool = False,
    system_prompt: str = "",
    preferred_provider: Optional[str] = None,
    timeout: int = 120,
) -> str:
    """Convenience function for global LLM execution."""
    client = get_llm_client()
    text, _ = client.dispatch(prompt, json_mode, system_prompt, preferred_provider, timeout)
    return text
