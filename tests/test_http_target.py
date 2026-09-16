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
