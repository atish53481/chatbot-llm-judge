"""FastAPI backend: the control panel behind the Chrome sidebar and dashboard."""
from __future__ import annotations

import os
import re
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

from backend import storage
from backend.dashboard.runner import judge_one, run_spec
from backend.datasets import goldens as goldens_store
from backend.judges.judge import build_judge, judge_name
from backend.metrics_catalog import ALL_SPECS, DEFAULT_THEME, SPECS_BY_KEY
from backend.targets import presets
from backend.targets.base import ChatbotClient
from backend.targets.dom_relay import DomRelayTargetClient, RelayQueue
from backend.targets.http_client import HttpTargetClient
from backend.targets.mock import MockTargetClient

DB_PATH = os.getenv("JUDGE_DB_PATH", "judge.db")
TARGET_TYPES = ("mock", "http", "dom")
PRESETS = {
    "openai_compatible": presets.openai_compatible,
    "commandcode": presets.commandcode,
}
PRESET_ARGS = ("base_url", "api_key", "model")
RELAY_WAIT_SECONDS = 20.0  # long-poll window, under the extension worker's 30s idle limit


def extension_origin_regex(extension_id: str | None) -> str:
    """Origins allowed to call the API: one pinned extension id, or any extension."""
    if extension_id:
        return rf"^chrome-extension://{re.escape(extension_id)}$"
    return r"^chrome-extension://[a-z]{32}$"


app = FastAPI(title="Chrome Sidebar LLM Judge")
# Only the extension may call this API: with "*", any page the user visits
# could read stored target API keys and spend judge credits. Request bodies
# must be JSON (FastAPI refuses other content types), which forces a CORS
# preflight, so other sites cannot send POSTs either.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=extension_origin_regex(os.getenv("JUDGE_EXTENSION_ID")),
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)
# Blocks DNS-rebinding pages that reach 127.0.0.1 under their own host name.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

_conn = storage.init_db(DB_PATH)
storage.seed_sample_target_once(_conn)
_relay_queue = RelayQueue()


class TargetCreate(BaseModel):
    name: str = Field(min_length=1)
    type: str
    config: dict = Field(default_factory=dict)
    preset: str | None = None


class GoldenCreate(BaseModel):
    theme: str = Field(min_length=1)
    question: str = Field(min_length=1)
    expected_answer: str = Field(min_length=1)
    context: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=list)


class RunRequest(BaseModel):
    target_id: int
    metric_key: str


class RelayMessage(BaseModel):
    session_id: str = Field(min_length=1)
    question_id: str = Field(min_length=1)
    text: str = Field(max_length=20000)


class RelayPoll(BaseModel):
    session_id: str = Field(min_length=1)
    wait_seconds: float = Field(default=RELAY_WAIT_SECONDS, ge=0, le=25)


class JudgeOneRequest(BaseModel):
    metric_key: str
    question: str = Field(min_length=1)
    actual_output: str = Field(min_length=1)
    theme: str | None = None
    expected_answer: str = ""
    context: list[str] = Field(default_factory=list)


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    target_id: int
    message: str = Field(min_length=1)
    history: list[ChatTurn] = Field(default_factory=list, max_length=40)


def _build_target(row: dict) -> ChatbotClient:
    config = row["config"]
    if row["type"] == "mock":
        return MockTargetClient()
    if row["type"] == "http":
        return HttpTargetClient(config)
    if row["type"] == "dom":
        session_id = config.get("session_id") or str(row["id"])
        return DomRelayTargetClient(session_id=session_id, queue=_relay_queue)
    raise ValueError(f"unknown target type {row['type']!r}")


def _client_or_400(row: dict) -> ChatbotClient:
    try:
        return _build_target(row)
    except (KeyError, TypeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"invalid target config: {e}") from e


def _target_or_404(target_id: int) -> dict:
    row = storage.get_target(_conn, target_id)
    if row is None:
        raise HTTPException(status_code=404, detail="target not found")
    return row


def _public(row: dict) -> dict:
    """A target as the extension sees it: header values (API keys) masked."""
    config = dict(row["config"])
    if config.get("headers"):
        config["headers"] = {name: "***" for name in config["headers"]}
    return {**row, "config": config}


def _apply_preset(body: TargetCreate) -> dict:
    """Expand a named preset into a full HTTP config (see targets/presets.py)."""
    if body.preset is None:
        return body.config
    if body.type != "http" or body.preset not in PRESETS:
        raise HTTPException(
            status_code=400, detail=f"unknown preset {body.preset!r} for type {body.type!r}"
        )
    args = {k: v for k, v in body.config.items() if k in PRESET_ARGS}
    extras = {k: v for k, v in body.config.items() if k not in PRESET_ARGS}
    try:
        return {**extras, **PRESETS[body.preset](**args)}
    except TypeError as e:  # base_url or api_key missing
        raise HTTPException(status_code=400, detail=f"preset {body.preset}: {e}") from e


@app.get("/api/status")
def api_status():
    return {"judge": {"model": judge_name(), "up": bool(os.getenv("JUDGE_API_KEY"))}}


@app.get("/api/targets")
def api_list_targets():
    return [_public(row) for row in storage.list_targets(_conn)]


@app.post("/api/targets")
def api_create_target(body: TargetCreate):
    if body.type not in TARGET_TYPES:
        raise HTTPException(
            status_code=400, detail=f"type must be one of: {', '.join(TARGET_TYPES)}"
        )
    config = _apply_preset(body)
    # Build the client once so a broken config is rejected before it is stored.
    _client_or_400({"id": 0, "type": body.type, "config": config})
    target_id = storage.add_target(_conn, body.name, body.type, config)
    return _public(storage.get_target(_conn, target_id))


@app.delete("/api/targets/{target_id}")
def api_delete_target(target_id: int):
    _target_or_404(target_id)
    storage.delete_target(_conn, target_id)
    return {"deleted": target_id}


@app.get("/api/goldens")
def api_list_goldens(theme: str | None = None):
    return goldens_store.load_goldens(theme=theme)


@app.post("/api/goldens")
def api_create_golden(body: GoldenCreate):
    return goldens_store.add_golden(
        body.theme, body.question, body.expected_answer, body.context, body.categories
    )


@app.put("/api/goldens/{golden_id}")
def api_update_golden(golden_id: str, body: GoldenCreate):
    updated = goldens_store.update_golden(
        golden_id,
        body.theme,
        body.question,
        body.expected_answer,
        body.context,
        body.categories,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="golden not found")
    return updated


@app.delete("/api/goldens/{golden_id}")
def api_delete_golden(golden_id: str):
    if not goldens_store.delete_golden(golden_id):
        raise HTTPException(status_code=404, detail="golden not found")
    return {"deleted": golden_id}


@app.get("/api/metrics")
def api_list_metrics():
    return [
        {"key": s.key, "title": s.title, "threshold": s.threshold, "category": s.category}
        for s in ALL_SPECS
    ]


@app.post("/api/run")
def api_run(req: RunRequest):
    row = _target_or_404(req.target_id)
    spec = SPECS_BY_KEY.get(req.metric_key)
    if spec is None:
        raise HTTPException(status_code=404, detail="metric not found")
    target = _client_or_400(row)
    try:
        judge = build_judge()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    theme = row["config"].get("theme") or DEFAULT_THEME
    return run_spec(spec, judge, target, req.target_id, _conn, theme=theme)


@app.post("/api/judge")
def api_judge(body: JudgeOneRequest):
    """Scores one answer the user collected by hand. Nothing is persisted.

    Reference-based metrics need a golden answer or context; when the question
    is one of the theme's goldens, its row supplies them.
    """
    spec = SPECS_BY_KEY.get(body.metric_key)
    if spec is None:
        raise HTTPException(status_code=404, detail="metric not found")
    expected, context = body.expected_answer, body.context
    if body.theme and (not expected or not context):
        golden = next(
            (g for g in goldens_store.load_goldens(theme=body.theme)
             if g["question"] == body.question),
            None,
        )
        if golden:
            expected = expected or golden["expected_answer"]
            context = context or golden["context"]
    try:
        judge = build_judge()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return judge_one(spec, judge, body.question, body.actual_output, expected, context)


@app.post("/api/chat")
def api_chat(req: ChatRequest):
    target = _client_or_400(_target_or_404(req.target_id))
    history = [turn.model_dump() for turn in req.history]
    try:
        reply = target.chat(req.message, history=history)
    except Exception as e:  # noqa: BLE001 - any chatbot failure is a bad gateway here
        raise HTTPException(status_code=502, detail=f"{type(e).__name__}: {e}") from e
    return {"reply": reply.reply, "model": reply.model, "mode": reply.mode}


@app.get("/api/runs/latest")
def api_runs_latest(target_id: int):
    return storage.latest_runs(_conn, target_id)


@app.get("/api/history")
def api_history(target_id: int, metric_key: str):
    return storage.history(_conn, target_id, metric_key)


def _known_session_or_404(session_id: str) -> None:
    # Tells a page that no longer backs a web-page chatbot to stop relaying.
    if not storage.has_dom_session(_conn, session_id):
        raise HTTPException(status_code=404, detail="no web-page chatbot uses this page")


@app.post("/api/relay")
def api_relay(body: RelayMessage):
    _known_session_or_404(body.session_id)
    _relay_queue.push(body.session_id, body.question_id, body.text)
    return {"relayed": True}


@app.post("/api/relay/next")
def api_relay_next(body: RelayPoll):
    # Long-poll: waits up to wait_seconds for the next question for this page.
    _known_session_or_404(body.session_id)
    question = _relay_queue.wait_question(body.session_id, body.wait_seconds)
    return question or {"id": None, "question": None}
