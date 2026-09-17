"""Named config factories that pre-fill HttpTargetClient for common shapes."""
from __future__ import annotations

COMMANDCODE_BASE_URL = "https://api.commandcode.ai/provider/v1"
COMMANDCODE_DEFAULT_MODEL = "z-ai/glm-5.3-flash"


def openai_compatible(base_url: str, api_key: str, model: str = "gpt-4o-mini") -> dict:
    """Config for any OpenAI-chat-completions-shaped API.

    base_url is the server root (e.g. https://api.openai.com). A trailing /v1,
    as most providers print it, is dropped because chat_path starts with /v1.
    """
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    return {
        "base_url": root,
        "chat_path": "/v1/chat/completions",
        "health_path": "/health",
        "request_format": "openai_messages",
        "model": model,
        "response_path": "choices.0.message.content",
        "headers": {"Authorization": f"Bearer {api_key}"},
    }


def commandcode(
    api_key: str,
    model: str = COMMANDCODE_DEFAULT_MODEL,
    base_url: str = COMMANDCODE_BASE_URL,
) -> dict:
    """Command Code's Provider API: OpenAI-compatible, hosted endpoint.

    Only the key is required. The endpoint serves open models (glm, qwen,
    deepseek, minimax, ...); Anthropic models live on /v1/messages and the
    openai/* models are not served here, so both are rejected with a 400.
    """
    return openai_compatible(base_url, api_key, model)
