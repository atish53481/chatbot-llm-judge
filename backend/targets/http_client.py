"""Generic, config-driven HTTP target connector.

One class covers "call any chatbot's REST API" without per-chatbot code:
the caller supplies where the message goes in the request and where the
reply text lives in the response (as a dotted path, list indices allowed
as numeric segments, e.g. "choices.0.message.content" or "0.generated_text").
"""
from __future__ import annotations

from typing import Any

import requests

from backend.targets.base import ChatbotClient, ChatReply

TIMEOUT = 60
REQUEST_FORMATS = ("flat", "openai_messages")
_TEXT_KEYS = ("chat_path", "health_path", "message_field", "response_path", "history_field")


def _get_path(obj: Any, dotted: str) -> Any:
    current = obj
    try:
        for part in dotted.split("."):
            if isinstance(current, list):
                current = current[int(part)]
            else:
                current = current[part]
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise ValueError(
            f"reply not found at response_path {dotted!r} ({type(e).__name__}: {e})"
        ) from e
    return current


class HttpTargetClient(ChatbotClient):
    def __init__(self, config: dict):
        base_url = config.get("base_url")
        if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must be an http:// or https:// URL")
        for key in _TEXT_KEYS:
            if key in config and not isinstance(config[key], str):
                raise ValueError(f"{key} must be a string")
        headers = config.get("headers", {})
        if not isinstance(headers, dict) or not all(
            isinstance(name, str) and isinstance(value, str) for name, value in headers.items()
        ):
            raise ValueError("headers must map header names to string values")

        self.base_url = base_url.rstrip("/")
        self.chat_path = config.get("chat_path", "/chat")
        self.health_path = config.get("health_path", "/health")
        self.message_field = config.get("message_field", "message")
        self.response_path = config.get("response_path", "reply")
        # Where "flat" requests carry earlier turns; "" leaves them out.
        self.history_field = config.get("history_field", "history")
        self.headers = headers
        self.request_format = config.get("request_format", "flat")
        self.model = config.get("model")
        if self.request_format not in REQUEST_FORMATS:
            raise ValueError(
                f"request_format must be one of {REQUEST_FORMATS}, got {self.request_format!r}"
            )

    def health(self) -> dict:
        r = requests.get(f"{self.base_url}{self.health_path}", timeout=10)
        r.raise_for_status()
        return r.json()

    def chat(self, message: str, history: list[dict] | None = None) -> ChatReply:
        if self.request_format == "openai_messages":
            payload = {"messages": [*(history or []), {"role": "user", "content": message}]}
            if self.model:
                payload["model"] = self.model
        else:
            payload = {self.message_field: message}
            if history and self.history_field:
                payload[self.history_field] = history

        r = requests.post(
            f"{self.base_url}{self.chat_path}",
            json=payload,
            headers=self.headers,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        reply_text = _get_path(data, self.response_path)
        # Some APIs answer with a JSON list, which carries no model name.
        model = data.get("model", "unknown") if isinstance(data, dict) else "unknown"
        return ChatReply(reply=str(reply_text), model=str(model), mode="http")
