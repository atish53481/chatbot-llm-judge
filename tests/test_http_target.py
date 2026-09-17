import pytest

from unittest.mock import patch, MagicMock

from backend.targets.http_client import HttpTargetClient
from backend.targets import presets


def _fake_response(json_body, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_body
    resp.raise_for_status = MagicMock()
    return resp


@patch("backend.targets.http_client.requests.post")
def test_chat_extracts_reply_from_response_path(mock_post):
    mock_post.return_value = _fake_response({"data": {"text": "hello there"}})
    client = HttpTargetClient({
        "base_url": "http://localhost:9999",
        "chat_path": "/chat",
        "message_field": "message",
        "response_path": "data.text",
    })
    reply = client.chat("hi")
    assert reply.reply == "hello there"
    assert reply.mode == "http"
    sent_payload = mock_post.call_args.kwargs["json"]
    assert sent_payload == {"message": "hi"}


@patch("backend.targets.http_client.requests.get")
def test_health_hits_health_path(mock_get):
    mock_get.return_value = _fake_response({"status": "ok"})
    client = HttpTargetClient({"base_url": "http://localhost:9999"})
    assert client.health() == {"status": "ok"}
    mock_get.assert_called_once_with("http://localhost:9999/health", timeout=10)


def test_openai_compatible_preset_shape():
    config = presets.openai_compatible("http://localhost:8080", "sk-test")
    client = HttpTargetClient(config)
    assert client.headers["Authorization"] == "Bearer sk-test"
    assert config["response_path"] == "choices.0.message.content"
    assert config["request_format"] == "openai_messages"
    assert config["model"] == "gpt-4o-mini"


@patch("backend.targets.http_client.requests.post")
def test_openai_messages_format_posts_correct_payload(mock_post):
    mock_post.return_value = _fake_response({"choices": [{"message": {"content": "hello"}}]})
    config = presets.openai_compatible("http://localhost:8080", "sk-test", model="gpt-4o-mini")
    client = HttpTargetClient(config)
    reply = client.chat("hi")
    assert reply.reply == "hello"

    sent_payload = mock_post.call_args.kwargs["json"]
    assert sent_payload == {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
    }


@patch("backend.targets.http_client.requests.post")
def test_openai_messages_format_prepends_history(mock_post):
    mock_post.return_value = _fake_response({"choices": [{"message": {"content": "ok"}}]})
    config = presets.openai_compatible("http://localhost:8080", "sk-test")
    client = HttpTargetClient(config)

    history = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
    ]
    client.chat("second", history=history)

    sent_payload = mock_post.call_args.kwargs["json"]
    assert sent_payload["messages"] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "second"},
    ]


def test_unknown_request_format_raises_valueerror():
    try:
        HttpTargetClient({
            "base_url": "http://localhost:9999",
            "request_format": "unknown",
        })
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "request_format" in str(e)
        assert "unknown" in str(e)


@patch("backend.targets.http_client.requests.post")
def test_list_shaped_response_is_supported(mock_post):
    mock_post.return_value = _fake_response([{"generated_text": "hi from a list"}])
    client = HttpTargetClient({"base_url": "http://localhost:9999", "response_path": "0.generated_text"})
    reply = client.chat("hi")
    assert reply.reply == "hi from a list"
    assert reply.model == "unknown"


@patch("backend.targets.http_client.requests.post")
def test_missing_reply_path_names_the_path(mock_post):
    mock_post.return_value = _fake_response({"data": {}})
    client = HttpTargetClient({"base_url": "http://localhost:9999", "response_path": "data.text"})
    with pytest.raises(ValueError, match="response_path 'data.text'"):
        client.chat("hi")


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"base_url": 123},
        {"base_url": "ftp://example.com"},
        {"base_url": "http://localhost:9999", "headers": [{"Authorization": "x"}]},
        {"base_url": "http://localhost:9999", "headers": {"Authorization": 5}},
        {"base_url": "http://localhost:9999", "chat_path": 7},
    ],
)
def test_invalid_config_raises_valueerror(config):
    with pytest.raises(ValueError):
        HttpTargetClient(config)


@patch("backend.targets.http_client.requests.post")
def test_flat_history_uses_the_configured_field(mock_post):
    mock_post.return_value = _fake_response({"reply": "ok"})
    history = [{"role": "user", "content": "first"}]
    HttpTargetClient({"base_url": "http://localhost:9999", "history_field": "context"}).chat("x", history=history)
    assert mock_post.call_args.kwargs["json"] == {"message": "x", "context": history}
    HttpTargetClient({"base_url": "http://localhost:9999", "history_field": ""}).chat("x", history=history)
    assert mock_post.call_args.kwargs["json"] == {"message": "x"}


def test_openai_preset_drops_a_trailing_v1():
    config = presets.openai_compatible("https://api.openai.com/v1/", "sk-test")
    assert config["base_url"] == "https://api.openai.com"
    assert config["chat_path"] == "/v1/chat/completions"


def test_commandcode_preset_needs_only_a_key():
    config = presets.commandcode("cmd-test")
    assert config["base_url"] == "https://api.commandcode.ai/provider"
    assert config["chat_path"] == "/v1/chat/completions"
    assert config["model"] == presets.COMMANDCODE_DEFAULT_MODEL
    assert config["headers"] == {"Authorization": "Bearer cmd-test"}
    # The config must be accepted by the client it is written for.
    assert HttpTargetClient(config).chat_path == "/v1/chat/completions"


def test_commandcode_preset_takes_a_model_override():
    config = presets.commandcode("cmd-test", model="z-ai/glm-5.3-flash")
    assert config["model"] == "z-ai/glm-5.3-flash"
