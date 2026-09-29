import json

import pytest

from backend.targets import presets


def test_llm_config_is_an_openai_chat_request():
    cfg = presets.build_llm_config(
        "https://api.groq.com/openai/v1/", "openai/gpt-oss-120b", "key", "Be brief."
    )
    assert cfg["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert cfg["method"] == "POST"
    assert cfg["headers"] == {"Content-Type": "application/json", "Authorization": "Bearer key"}
    assert cfg["response_path"] == "choices.0.message.content"
    assert cfg["history_path"] == "messages"
    body = json.loads(cfg["body_template"])
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["messages"][0] == {"role": "system", "content": "Be brief."}
    assert body["messages"][-1] == {"role": "user", "content": "{{message}}"}


def test_llm_config_without_a_key_has_no_authorization_header():
    cfg = presets.build_llm_config("https://localhost:11434/v1", "llama3")
    assert "Authorization" not in cfg["headers"]
    assert json.loads(cfg["body_template"])["messages"][0]["role"] == "user"


@pytest.mark.parametrize(
    "kwargs",
    [{"base_url": "ftp://x", "model": "m"}, {"base_url": "/relative", "model": "m"}, {"model": ""}],
)
def test_llm_config_rejects_bad_input(kwargs):
    with pytest.raises(ValueError):
        presets.build_llm_config(**kwargs)
