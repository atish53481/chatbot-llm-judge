"""Generic, config-driven HTTP target connector.

One class covers "call any chatbot's REST API" without per-chatbot code:
the caller supplies where the message goes in the request and where the
reply text lives in the response (as a dotted path, list indices allowed
as numeric segments, e.g. "choices.0.message.content").
"""
from __future__ import annotations

from typing import Any

import requests

from backend.targets.base import ChatbotClient, ChatReply

TIMEOUT = 60


def _get_path(obj: Any, dotted: str) -> Any:
    current = obj
    for part in dotted.split("."):
        if isinstance(current, list):
            current = current[int(part)]
        else:
            current = current[part]
    return current


class HttpTargetClient(ChatbotClient):
    def __init__(self, config: dict):
        self.base_url = config["base_url"].rstrip("/")
        self.chat_path = config.get("chat_path", "/chat")
        self.health_path = config.get("health_path", "/health")
        self.message_field = config.get("message_field", "message")
        self.response_path = config.get("response_path", "reply")
        self.headers = config.get("headers", {})
        self.request_format = config.get("request_format", "flat")
        self.model = config.get("model")

        # Validate request_format
        valid_formats = {"flat", "openai_messages"}
        if self.request_format not in valid_formats:
            raise ValueError(
                f"request_format must be one of {valid_formats}, got {self.request_format!r}"
            )

    def health(self) -> dict:
        r = requests.get(f"{self.base_url}{self.health_path}", timeout=10)
        r.raise_for_status()
        return r.json()

    def chat(self, message: str, history: list[dict] | None = None) -> ChatReply:
        # Build payload based on request_format
        if self.request_format == "flat":
            payload = {self.message_field: message}
            if history:
                payload["history"] = history
        elif self.request_format == "openai_messages":
            messages = list(history or [])
            messages.append({"role": "user", "content": message})
            payload = {"messages": messages}
            if self.model:
                payload["model"] = self.model
        else:
            # This should never happen due to validation in __init__
            raise ValueError(f"Unknown request_format: {self.request_format}")

        r = requests.post(
            f"{self.base_url}{self.chat_path}",
            json=payload,
            headers=self.headers,
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        reply_text = _get_path(data, self.response_path)
        return ChatReply(reply=str(reply_text), model=data.get("model", "unknown"), mode="http")
