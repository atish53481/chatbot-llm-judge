"""The chatbot connector: replays a request captured from a real website.

A chatbot on a website has no documented API, but the browser's network tab
shows the exact request its page sends. The user pastes that request as
cURL (see targets/curl.py); the config keeps its url, method, headers and
body, with {{message}} marking where the question goes. The reply is read
from the JSON response at a dotted path (list indices allowed as numeric
segments, e.g. "choices.0.message.content"), from a server-sent-events
stream by joining that path across events, or as the raw text when no path
is given.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, quote_plus

import requests

from backend import usage
from backend.targets.base import ChatbotClient, ChatReply
from backend.targets.curl import MESSAGE_PLACEHOLDER, context_texts, find_history_path

TIMEOUT = 60
METHODS = ("GET", "POST", "PUT", "PATCH")
_TEXT_KEYS = ("url", "method", "body_template", "response_path", "context_path", "history_path")
# Headers from the captured request that would be wrong for a new body.
_DROPPED_HEADERS = ("content-length", "host", "accept-encoding")
_PREVIEW_CHARS = 400


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


def _preview(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _PREVIEW_CHARS else text[:_PREVIEW_CHARS] + "…"


def is_stream(r: requests.Response) -> bool:
    return "text/event-stream" in r.headers.get("Content-Type", "") or r.text.lstrip().startswith("data:")


def sse_events(text: str) -> list[Any]:
    """Decoded `data:` payloads of a server-sent-events body, in order."""
    events = []
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            events.append(json.loads(payload))
        except ValueError:
            events.append(payload)
    return events


class HttpTargetClient(ChatbotClient):
    def __init__(self, config: dict):
        for key in _TEXT_KEYS:
            if key in config and not isinstance(config[key], str):
                raise ValueError(f"{key} must be a string")
        url = config.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            raise ValueError("url must be an http:// or https:// URL")
        headers = config.get("headers", {})
        if not isinstance(headers, dict) or not all(
            isinstance(name, str) and isinstance(value, str) for name, value in headers.items()
        ):
            raise ValueError("headers must map header names to string values")
        method = config.get("method", "POST").upper()
        if method not in METHODS:
            raise ValueError(f"method must be one of {METHODS}")
        body_template = config.get("body_template", "")
        if MESSAGE_PLACEHOLDER not in url + body_template:
            raise ValueError(
                f"put {MESSAGE_PLACEHOLDER} in the body (or URL) where the question goes"
            )
        if method == "GET" and body_template:
            raise ValueError("a GET request has no body; put the placeholder in the URL")

        self.url = url
        self.method = method
        self.headers = headers
        self.body_template = body_template
        self.response_path = config.get("response_path", "")
        # Where the response lists the retrieved documents (RAG metrics); "" = none.
        self.context_path = config.get("context_path", "")
        # Where the body carries earlier turns (multi-turn metrics). Older configs
        # have none saved, so it is read from the body ("history": [], "messages").
        self.history_path = config.get("history_path") or find_history_path(body_template)

    def _encode_for_body(self, message: str) -> str:
        content_type = next(
            (v for k, v in self.headers.items() if k.lower() == "content-type"), ""
        ).lower()
        if "x-www-form-urlencoded" in content_type:
            return quote_plus(message)
        # json.dumps escapes quotes, newlines and backslashes, so the question
        # drops into a JSON string literal without breaking the body.
        return json.dumps(message, ensure_ascii=False)[1:-1]

    def chat(self, message: str, history: list[dict] | None = None) -> ChatReply:
        # Golden questions are asked on their own (no history); multi-turn
        # scenarios pass the conversation so far, which goes into history_path.
        return self.read_reply(self.send(message, history))

    def _with_history(self, body: str, history: list[dict]) -> str:
        """Puts earlier turns ({role, content}) where the captured body keeps them."""
        try:
            data = json.loads(body)
        except ValueError:
            return body  # form bodies carry no conversation
        if not isinstance(data, dict) or self.history_path not in data:
            return body
        turns = [{"role": t["role"], "content": t["content"]} for t in history]
        if self.history_path == "messages":
            # Chat-style body: earlier turns go between the system prompt and this question.
            current = data["messages"]
            system = [m for m in current[:-1] if isinstance(m, dict) and m.get("role") == "system"]
            data["messages"] = system + turns + current[-1:]
        else:
            data[self.history_path] = turns
        return json.dumps(data, ensure_ascii=False)

    def send(self, message: str, history: list[dict] | None = None) -> requests.Response:
        """Sends one question; raises ValueError on an HTTP error status."""
        url = self.url.replace(MESSAGE_PLACEHOLDER, quote(message, safe=""))
        body = None
        if self.body_template:
            text = self.body_template.replace(MESSAGE_PLACEHOLDER, self._encode_for_body(message))
            if history and self.history_path:
                text = self._with_history(text, history)
            body = text.encode("utf-8")
        headers = {
            k: v for k, v in self.headers.items() if k.lower() not in _DROPPED_HEADERS
        }
        r = requests.request(self.method, url, data=body, headers=headers, timeout=TIMEOUT)
        usage.record_target()
        if not r.ok:
            raise ValueError(f"HTTP {r.status_code} from {url}: {_preview(r.text)}")
        return r

    def read_reply(self, r: requests.Response) -> ChatReply:
        text = r.text
        if is_stream(r):
            return ChatReply(reply=self._reply_from_stream(text), model="unknown", mode="http")
        if not self.response_path:
            return ChatReply(reply=text, model="unknown", mode="http")
        try:
            data = r.json()
        except ValueError as e:
            raise ValueError(f"response is not JSON: {_preview(text)}") from e
        try:
            reply_text = _get_path(data, self.response_path)
        except ValueError as e:
            raise ValueError(f"{e}. Response was: {_preview(text)}") from e
        # Some APIs answer with a JSON list, which carries no model name.
        model = data.get("model", "unknown") if isinstance(data, dict) else "unknown"
        retrieval = None
        if self.context_path:
            try:
                retrieval = context_texts(_get_path(data, self.context_path)) or None
            except ValueError:
                retrieval = None  # this answer came without sources
        return ChatReply(reply=str(reply_text), model=str(model), mode="http",
                         retrieval_context=retrieval)

    def _reply_from_stream(self, text: str) -> str:
        events = sse_events(text)
        if not self.response_path:
            return "".join(e if isinstance(e, str) else json.dumps(e) for e in events)
        pieces = []
        for event in events:
            try:
                piece = _get_path(event, self.response_path)
            except ValueError:
                continue  # role/usage/metadata events carry no text
            if isinstance(piece, str):
                pieces.append(piece)
        if not pieces:
            raise ValueError(
                f"no stream event has text at response_path {self.response_path!r}; "
                f"first events: {_preview(json.dumps(events[:3], ensure_ascii=False))}"
            )
        return "".join(pieces)
