"""Presets that fill the HTTP connector for a known API shape.

The connector replays an HTTP request (see http_client.py). An LLM with an
OpenAI-compatible chat API speaks that same shape, so judging another model
needs no new connector: this builds the url, headers and JSON body, and the
side panel fills the ordinary chatbot form with them. The target is then
stored as a plain http chatbot, so the runner and every metric work unchanged.
"""
from __future__ import annotations

import json
from urllib.parse import urlsplit

OPENAI_COMPATIBLE = "openai_compatible"
PRESETS = (OPENAI_COMPATIBLE,)
DEFAULT_BASE_URL = "https://api.openai.com/v1"
# Where an OpenAI-compatible chat API puts the reply text.
RESPONSE_PATH = "choices.0.message.content"


def _require_http_url(url: str, what: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url.startswith(("http://", "https://")) or not urlsplit(url).netloc:
        raise ValueError(f"{what} must be an http:// or https:// URL")
    return url


def build_llm_config(
    base_url: str = DEFAULT_BASE_URL,
    model: str = "",
    api_key: str = "",
    system_prompt: str = "",
) -> dict:
    """The connector config for an OpenAI-compatible /chat/completions API.

    A key is optional: a local model usually needs none, so the Authorization
    header is only added when one is given.
    """
    base = _require_http_url(base_url, "base URL")
    model = (model or "").strip()
    if not model:
        raise ValueError("model is required")
    headers = {"Content-Type": "application/json"}
    key = (api_key or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    messages: list[dict] = []
    prompt = (system_prompt or "").strip()
    if prompt:
        messages.append({"role": "system", "content": prompt})
    messages.append({"role": "user", "content": "{{message}}"})
    body = {"model": model, "messages": messages, "stream": False}
    return {
        "url": f"{base}/chat/completions",
        "method": "POST",
        "headers": headers,
        "body_template": json.dumps(body, ensure_ascii=False),
        "response_path": RESPONSE_PATH,
        "context_path": "",
        # Chat-style body: earlier turns go into messages for the multi-turn metrics.
        "history_path": "messages",
    }
