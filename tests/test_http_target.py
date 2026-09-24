import json
from unittest.mock import MagicMock, patch

import pytest

from backend.targets.http_client import HttpTargetClient

URL = "https://shop.example/api/chat"


def _config(**overrides):
    config = {
        "url": URL,
        "method": "POST",
        "headers": {"Content-Type": "application/json", "Cookie": "s=1", "Content-Length": "99"},
        "body_template": '{"conversation_id": "c1", "message": "{{message}}"}',
        "response_path": "data.text",
    }
    config.update(overrides)
    return config


def _response(text, content_type="application/json", status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.ok = status < 400
    resp.text = text
    resp.headers = {"Content-Type": content_type}
    resp.json.side_effect = lambda: json.loads(text)
    return resp


@patch("backend.targets.http_client.requests.request")
def test_fills_message_into_json_body_and_reads_reply(mock_request):
    mock_request.return_value = _response('{"data": {"text": "hello there"}}')
    reply = HttpTargetClient(_config()).chat('say "hi"\nnow')
    assert reply.reply == "hello there"
    assert reply.mode == "http"
    method, url = mock_request.call_args.args
    assert (method, url) == ("POST", URL)
    sent = json.loads(mock_request.call_args.kwargs["data"])
    assert sent == {"conversation_id": "c1", "message": 'say "hi"\nnow'}
    headers = mock_request.call_args.kwargs["headers"]
    assert headers["Cookie"] == "s=1"
    assert "Content-Length" not in headers


@patch("backend.targets.http_client.requests.request")
def test_form_body_is_url_encoded(mock_request):
    mock_request.return_value = _response('{"answer": "ok"}')
    config = _config(
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body_template="q={{message}}&lang=en",
        response_path="answer",
    )
    HttpTargetClient(config).chat("a b&c")
    assert mock_request.call_args.kwargs["data"] == b"q=a+b%26c&lang=en"


@patch("backend.targets.http_client.requests.request")
def test_get_puts_message_in_url(mock_request):
    mock_request.return_value = _response("plain answer", content_type="text/plain")
    config = _config(method="GET", url=URL + "?q={{message}}", body_template="", response_path="")
    reply = HttpTargetClient(config).chat("where is it?")
    assert mock_request.call_args.args[1] == URL + "?q=where%20is%20it%3F"
    assert mock_request.call_args.kwargs["data"] is None
    assert reply.reply == "plain answer"


@patch("backend.targets.http_client.requests.request")
def test_stream_joins_text_across_events(mock_request):
    stream = (
        'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    mock_request.return_value = _response(stream, content_type="text/event-stream")
    reply = HttpTargetClient(_config(response_path="choices.0.delta.content")).chat("hi")
    assert reply.reply == "Hello"


@patch("backend.targets.http_client.requests.request")
def test_missing_reply_path_shows_the_response(mock_request):
    mock_request.return_value = _response('{"other": 1}')
    with pytest.raises(ValueError, match=r"data\.text.*Response was: \{\"other\": 1\}"):
        HttpTargetClient(_config()).chat("hi")


@patch("backend.targets.http_client.requests.request")
def test_http_error_shows_status_and_body(mock_request):
    mock_request.return_value = _response("forbidden: csrf", content_type="text/plain", status=403)
    with pytest.raises(ValueError, match="HTTP 403.*csrf"):
        HttpTargetClient(_config()).chat("hi")


@pytest.mark.parametrize("config", [
    _config(url="ftp://x"),
    _config(url=None),
    _config(body_template='{"message": "fixed"}'),
    _config(method="DELETE"),
    _config(method="GET", url=URL + "?q={{message}}"),
    _config(headers={"x": 1}),
    _config(response_path=3),
])
def test_invalid_config_raises_valueerror(config):
    with pytest.raises(ValueError):
        HttpTargetClient(config)


@patch("backend.targets.http_client.requests.request")
def test_history_goes_where_the_body_keeps_it(mock_request):
    mock_request.return_value = _response('{"data": {"text": "ok"}}')
    config = _config(body_template='{"message": "{{message}}", "history": []}')
    history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    HttpTargetClient(config).chat("again", history=history)
    assert json.loads(mock_request.call_args.kwargs["data"]) == {"message": "again", "history": history}


@patch("backend.targets.http_client.requests.request")
def test_chat_style_messages_get_history_before_the_question(mock_request):
    mock_request.return_value = _response('{"choices": [{"message": {"content": "ok"}}]}')
    config = _config(
        body_template='{"model": "m", "messages": [{"role": "system", "content": "be nice"}, '
                      '{"role": "user", "content": "{{message}}"}]}',
        response_path="choices.0.message.content")
    HttpTargetClient(config).chat("q2", history=[{"role": "user", "content": "q1"},
                                                 {"role": "assistant", "content": "a1"}])
    sent = json.loads(mock_request.call_args.kwargs["data"])["messages"]
    assert [m["content"] for m in sent] == ["be nice", "q1", "a1", "q2"]


@patch("backend.targets.http_client.requests.request")
def test_retrieved_context_is_read_from_context_path(mock_request):
    mock_request.return_value = _response(json.dumps({
        "data": {"text": "answer"},
        "sources": [{"title": "Refunds", "content": "Refunds take 7 days."}, "Returns: 30 days."],
    }))
    reply = HttpTargetClient(_config(context_path="sources")).chat("q")
    assert reply.retrieval_context == ["Refunds take 7 days.", "Returns: 30 days."]
    assert HttpTargetClient(_config()).chat("q").retrieval_context is None


from backend import usage


@patch("backend.targets.http_client.requests.request")
def test_send_counts_a_target_call(mock_request):
    usage.reset()
    mock_request.return_value = _response('{"data": {"text": "ok"}}')
    HttpTargetClient(_config()).chat("hi")
    assert usage.snapshot()["target_calls"] == 1
    usage.reset()
