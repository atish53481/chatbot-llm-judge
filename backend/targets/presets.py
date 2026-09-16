"""Named config factories that pre-fill HttpTargetClient for common shapes."""
from __future__ import annotations


def openai_compatible(base_url: str, api_key: str, model: str = "gpt-4o-mini") -> dict:
    """Config for any OpenAI-chat-completions-shaped API."""
    return {
        "base_url": base_url,
        "chat_path": "/v1/chat/completions",
        "health_path": "/health",
        "request_format": "openai_messages",
        "model": model,
        "response_path": "choices.0.message.content",
        "headers": {"Authorization": f"Bearer {api_key}"},
    }
