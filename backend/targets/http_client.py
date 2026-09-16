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

    def health(self) -> dict:
        r = requests.get(f"{self.base_url}{self.health_path}", timeout=10)
        r.raise_for_status()
        return r.json()

    def chat(self, message: str, history: list[dict] | None = None) -> ChatReply:
        payload = {self.message_field: message}
        if history:
            payload["history"] = history
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
