"""FastAPI backend: the control panel behind the Chrome sidebar and dashboard."""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

from backend import storage
from backend.dashboard.runner import run_spec
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
PRESETS = {"openai_compatible": presets.openai_compatible}
PRESET_ARGS = ("base_url", "api_key", "model")

app = FastAPI(title="Chrome Sidebar LLM Judge")
# Only the extension may call this API: with "*", any page the user visits
# could read stored target API keys and spend judge credits.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^chrome-extension://[a-z]{32}$",
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)
# Blocks DNS-rebinding pages that reach 127.0.0.1 under their own host name.
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

_conn = storage.init_db(DB_PATH)
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
    text: str


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


@app.get("/api/runs/latest")
def api_runs_latest(target_id: int):
    return storage.latest_runs(_conn, target_id)


@app.get("/api/history")
def api_history(target_id: int, metric_key: str):
    return storage.history(_conn, target_id, metric_key)


@app.post("/api/relay")
def api_relay(body: RelayMessage):
    _relay_queue.push(body.session_id, body.question_id, body.text)
    return {"relayed": True}


@app.get("/api/relay/next")
def api_relay_next(session_id: str):
    return _relay_queue.next_question(session_id) or {"id": None, "question": None}
