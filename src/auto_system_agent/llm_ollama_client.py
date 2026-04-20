"""Dedicated OLLAMA client. All model answers flow through this client.

Supports both OpenAI-compatible endpoint (http://localhost:11434/v1/chat/completions)
and native Ollama endpoint (http://localhost:11434/api/chat). Tools remain local;
the model only decides intent and generates conversational replies.
"""

import json
import os
from typing import Any
from urllib import error, request


OLLAMA_DEFAULT_URL = "http://localhost:11434/v1/chat/completions"
OLLAMA_DEFAULT_MODEL = "llama3.1"
OLLAMA_DEFAULT_TIMEOUT = 30.0


def resolve_ollama_config(config: dict | None = None) -> dict[str, Any]:
    config = config or {}
    url = (
        str(config.get("url") or os.getenv("AUTO_AGENT_OLLAMA_URL", "")).strip()
        or str(config.get("url") or os.getenv("AUTO_AGENT_LLM_URL", "")).strip()
        or os.getenv("AUTO_AGENT_DEFAULT_LLM_URL", "").strip()
        or OLLAMA_DEFAULT_URL
    )
    api_key = (
        str(config.get("api_key") or os.getenv("AUTO_AGENT_OLLAMA_API_KEY", "")).strip()
        or str(config.get("api_key") or os.getenv("AUTO_AGENT_LLM_API_KEY", "")).strip()
    )
    model = (
        str(config.get("model") or os.getenv("AUTO_AGENT_OLLAMA_MODEL", "")).strip()
        or str(config.get("model") or os.getenv("AUTO_AGENT_LLM_MODEL", "")).strip()
        or os.getenv("AUTO_AGENT_DEFAULT_LLM_MODEL", "").strip()
        or OLLAMA_DEFAULT_MODEL
    )
    timeout_raw = str(
        config.get("timeout")
        or os.getenv("AUTO_AGENT_OLLAMA_TIMEOUT", "")
        or os.getenv("AUTO_AGENT_LLM_TIMEOUT", "")
        or os.getenv("AUTO_AGENT_DEFAULT_LLM_TIMEOUT", "")
        or str(OLLAMA_DEFAULT_TIMEOUT)
    ).strip()
    try:
        timeout = float(timeout_raw) if timeout_raw else OLLAMA_DEFAULT_TIMEOUT
    except ValueError:
        timeout = OLLAMA_DEFAULT_TIMEOUT

    return {"url": url, "api_key": api_key, "model": model, "timeout": timeout}


def ollama_chat(messages: list[dict[str, str]], config: dict | None = None) -> str | None:
    """Call OLLAMA chat and return the assistant content string, or None on failure."""
    cfg = resolve_ollama_config(config)
    url: str = cfg["url"]
    model: str = cfg["model"]
    timeout: float = cfg["timeout"]
    api_key: str = cfg["api_key"]

    payload = {"model": model, "messages": messages, "temperature": 0.2, "stream": False}
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Try OpenAI-compatible first
    try:
        req = request.Request(url, data=body, headers=headers, method="POST")
        with request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            if isinstance(content, str) and content.strip():
                return content.strip()
            # Fallback native shape
            content = data.get("message", {}).get("content", "")
            if isinstance(content, str) and content.strip():
                return content.strip()
    except (error.URLError, error.HTTPError, json.JSONDecodeError, TimeoutError, OSError):
        pass

    # Fallback to native /api/chat
    if url.endswith("/v1/chat/completions"):
        native_url = url.replace("/v1/chat/completions", "/api/chat")
        native_payload = {"model": model, "messages": messages, "stream": False}
        native_body = json.dumps(native_payload).encode("utf-8")
        try:
            native_req = request.Request(native_url, data=native_body, headers=headers, method="POST")
            with request.urlopen(native_req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content = data.get("message", {}).get("content", "")
                if isinstance(content, str) and content.strip():
                    return content.strip()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                if isinstance(content, str) and content.strip():
                    return content.strip()
        except Exception:
            return None

    return None
