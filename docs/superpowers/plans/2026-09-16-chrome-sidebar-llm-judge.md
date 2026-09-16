# Chrome Sidebar LLM Judge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Chrome sidebar extension + FastAPI backend that judges any chatbot's answers with DeepEval metrics, showing per-run and trend charts, with a mock target and default golden set for zero-setup use.

**Architecture:** FastAPI backend (`dashboard/app.py`) owns target connectors, a separate judge model, a golden dataset, a metrics catalog shared with pytest, and SQLite run history. MV3 Chrome extension sidebar drives it over `http://127.0.0.1:8000`; a dashboard tab renders chat + Chart.js charts.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, deepeval, pytest, SQLite (stdlib `sqlite3`), Chrome MV3 (`chrome.sidePanel`), vanilla JS, Chart.js (CDN).

**Spec:** `docs/superpowers/specs/2026-09-16-chrome-sidebar-llm-judge-design.md`

## Global Constraints

- Judge model is never the target chatbot being graded (separate API key: `JUDGE_API_KEY`).
- Backend fixed at `http://127.0.0.1:8000` — no auto-discovery.
- Golden rows: `{id, theme, question, expected_answer, context: [...], categories: [...]}`.
- `runs` table timestamps are ISO-8601 UTC strings (e.g. `2026-09-16T14:32:07Z`).
- No placeholders/mocked-out logic in shipped code — mock target is a real, complete implementation, not a stub.
- Extension permissions limited to `sidePanel`, `storage`, `scripting`, `host_permissions: ["http://127.0.0.1/*"]` plus user-added target origins added at runtime.

---

## File Structure

```
backend/
  storage.py                  # SQLite init + runs/targets CRUD
  targets/
    __init__.py
    base.py                   # ChatReply, ChatbotClient ABC
    mock.py                   # MockTargetClient
    http_client.py            # HttpTargetClient (generic config-driven)
    presets.py                # openai_compatible() factory
    dom_relay.py               # DomRelayTargetClient + relay queue
  judges/
    __init__.py
    judge.py                  # GroqJudge / build_judge (ported from reference)
  datasets/
    __init__.py
    goldens.py                 # Golden dataclass + JSON-backed store
    goldens.json                # seed data (general_support theme)
  metrics_catalog.py            # MetricSpec + ALL_SPECS/SPECS_BY_KEY
  dashboard/
    __init__.py
    runner.py                   # run_spec(): execute one metric against one target
    app.py                      # FastAPI app + all HTTP routes
tests/
  test_00_smoke.py
  test_storage.py
  test_mock_target.py
  test_http_target.py
  test_goldens.py
  test_judge.py
  test_metrics_catalog.py
  test_dom_relay.py
  test_runner.py
  test_app.py
requirements.txt                 # updated (existing file)
pytest.ini                       # new
run-backend.bat                  # new
ChatbotExtension/
  manifest.json                  # rewritten for MV3 sidePanel
  background.js                  # rewritten: opens side panel
  content_script.js              # DOM-relay content script
  sidebar/
    sidebar.html
    sidebar.js
    sidebar.css
  dashboard/
    dashboard.html
    dashboard.js
    dashboard.css
```

---

### Task 1: SQLite storage layer

**Files:**
- Create: `backend/__init__.py`, `backend/storage.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Produces: `storage.init_db(path: str) -> sqlite3.Connection`, `storage.add_target(conn, name, type_, config: dict) -> int`, `storage.get_target(conn, target_id: int) -> dict | None`, `storage.list_targets(conn) -> list[dict]`, `storage.delete_target(conn, target_id: int) -> None`, `storage.record_run(conn, target_id: int, metric_key: str, score: float | None, passed: bool, ts: str) -> int`, `storage.latest_runs(conn, target_id: int) -> list[dict]`, `storage.history(conn, target_id: int, metric_key: str) -> list[dict]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_storage.py
import sqlite3
from backend import storage


def test_init_db_creates_tables(tmp_path):
    conn = storage.init_db(str(tmp_path / "test.db"))
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"targets", "runs"} <= tables


def test_add_and_get_target(tmp_path):
    conn = storage.init_db(str(tmp_path / "test.db"))
    tid = storage.add_target(conn, "Sample (mock)", "mock", {})
    row = storage.get_target(conn, tid)
    assert row["name"] == "Sample (mock)"
    assert row["type"] == "mock"
    assert row["config"] == {}


def test_list_and_delete_target(tmp_path):
    conn = storage.init_db(str(tmp_path / "test.db"))
    tid = storage.add_target(conn, "T1", "mock", {})
    assert len(storage.list_targets(conn)) == 1
    storage.delete_target(conn, tid)
    assert storage.list_targets(conn) == []


def test_record_and_query_runs(tmp_path):
    conn = storage.init_db(str(tmp_path / "test.db"))
    tid = storage.add_target(conn, "T1", "mock", {})
    storage.record_run(conn, tid, "answer_relevancy", 0.9, True, "2026-09-16T10:00:00Z")
    storage.record_run(conn, tid, "answer_relevancy", 0.4, False, "2026-09-16T11:00:00Z")

    latest = storage.latest_runs(conn, tid)
    assert len(latest) == 1
    assert latest[0]["score"] == 0.4  # most recent per metric_key

    hist = storage.history(conn, tid, "answer_relevancy")
    assert [r["score"] for r in hist] == [0.9, 0.4]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_storage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend'` or `AttributeError`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/storage.py
"""SQLite persistence for targets and judge run history."""
from __future__ import annotations

import json
import sqlite3
from typing import Any


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            config_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_id INTEGER NOT NULL,
            metric_key TEXT NOT NULL,
            score REAL,
            passed INTEGER NOT NULL,
            ts TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def add_target(conn: sqlite3.Connection, name: str, type_: str, config: dict) -> int:
    cur = conn.execute(
        "INSERT INTO targets (name, type, config_json) VALUES (?, ?, ?)",
        (name, type_, json.dumps(config)),
    )
    conn.commit()
    return cur.lastrowid


def get_target(conn: sqlite3.Connection, target_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM targets WHERE id = ?", (target_id,)).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "config": json.loads(row["config_json"]),
    }


def list_targets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM targets ORDER BY id").fetchall()
    return [
        {"id": r["id"], "name": r["name"], "type": r["type"], "config": json.loads(r["config_json"])}
        for r in rows
    ]


def delete_target(conn: sqlite3.Connection, target_id: int) -> None:
    conn.execute("DELETE FROM targets WHERE id = ?", (target_id,))
    conn.commit()


def record_run(
    conn: sqlite3.Connection,
    target_id: int,
    metric_key: str,
    score: float | None,
    passed: bool,
    ts: str,
) -> int:
    cur = conn.execute(
        "INSERT INTO runs (target_id, metric_key, score, passed, ts) VALUES (?, ?, ?, ?, ?)",
        (target_id, metric_key, score, int(passed), ts),
    )
    conn.commit()
    return cur.lastrowid


def latest_runs(conn: sqlite3.Connection, target_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT r.* FROM runs r
        INNER JOIN (
            SELECT metric_key, MAX(id) AS max_id
            FROM runs WHERE target_id = ?
            GROUP BY metric_key
        ) latest ON r.id = latest.max_id
        ORDER BY r.metric_key
        """,
        (target_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def history(conn: sqlite3.Connection, target_id: int, metric_key: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM runs WHERE target_id = ? AND metric_key = ? ORDER BY id",
        (target_id, metric_key),
    ).fetchall()
    return [dict(r) for r in rows]
```

Also create `backend/__init__.py` (empty) so `backend` is importable as a package.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_storage.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/__init__.py backend/storage.py tests/test_storage.py
git commit -m "feat: add SQLite storage for targets and run history"
```

---

### Task 2: Target connector base + mock target

**Files:**
- Create: `backend/targets/__init__.py`, `backend/targets/base.py`, `backend/targets/mock.py`
- Create (stub, replaced in Task 4): `backend/datasets/__init__.py`, `backend/datasets/goldens.py`
- Test: `tests/test_mock_target.py`

**Interfaces:**
- Consumes: nothing (foundational)
- Produces: `ChatReply` dataclass (`reply: str`, `model: str`, `mode: str`), `ChatbotClient` ABC with `.health() -> dict` and `.chat(message: str, history: list[dict] | None = None) -> ChatReply`, `MockTargetClient` implementing it. Also `load_goldens(theme: str | None = None) -> list[dict]` (minimal stub here, full version in Task 4 with identical signature).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mock_target.py
from backend.targets.mock import MockTargetClient


def test_mock_health_reports_ok():
    client = MockTargetClient()
    assert client.health() == {"status": "ok"}


def test_mock_matches_known_question():
    client = MockTargetClient()
    reply = client.chat("What is your refund window?")
    assert "7 business days" in reply.reply
    assert reply.mode == "mock"
    assert reply.model == "mock-canned"


def test_mock_falls_back_for_unknown_question():
    client = MockTargetClient()
    reply = client.chat("What color is the sky on Mars?")
    assert reply.reply  # non-empty fallback
    assert reply.mode == "mock"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mock_target.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.targets'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/targets/__init__.py
```

```python
# backend/targets/base.py
"""Common shape every target connector implements."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ChatReply:
    reply: str
    model: str
    mode: str  # "mock" | "http" | "dom"


class ChatbotClient(ABC):
    @abstractmethod
    def health(self) -> dict:
        ...

    @abstractmethod
    def chat(self, message: str, history: list[dict] | None = None) -> ChatReply:
        ...
```

```python
# backend/targets/mock.py
"""Canned-response target. Zero setup, same interface as a real target."""
from __future__ import annotations

from difflib import SequenceMatcher

from backend.datasets.goldens import load_goldens
from backend.targets.base import ChatbotClient, ChatReply

FALLBACK = (
    "I don't have information about that. Please contact support@example.com."
)


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


class MockTargetClient(ChatbotClient):
    def health(self) -> dict:
        return {"status": "ok"}

    def chat(self, message: str, history: list[dict] | None = None) -> ChatReply:
        goldens = load_goldens()
        best = None
        best_score = 0.0
        for g in goldens:
            score = _similarity(message, g["question"])
            if score > best_score:
                best_score = score
                best = g
        reply_text = best["expected_answer"] if best and best_score > 0.6 else FALLBACK
        return ChatReply(reply=reply_text, model="mock-canned", mode="mock")
```

```python
# backend/datasets/__init__.py
```

```python
# backend/datasets/goldens.py
"""Temporary minimal version — replaced with full JSON-backed store in Task 4.
Signature (`load_goldens(theme=...)`) is kept identical so Task 4's rewrite
doesn't change any caller."""
from __future__ import annotations


def load_goldens(theme: str | None = None) -> list[dict]:
    return [
        {
            "id": "g_0001",
            "theme": "general_support",
            "question": "What is your refund window?",
            "expected_answer": (
                "Refunds are processed within 7 business days of receiving the "
                "returned item. Returns must be initiated within 30 days of delivery."
            ),
            "context": [
                "Refunds are processed within 7 business days of receiving the returned item.",
                "Items can be returned within 30 days of delivery in original condition.",
            ],
            "categories": ["policy", "refund"],
        }
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mock_target.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/targets backend/datasets
git commit -m "feat: add target connector base + mock target with canned goldens"
```

---

### Task 3: Generic HTTP target + presets

**Files:**
- Create: `backend/targets/http_client.py`, `backend/targets/presets.py`
- Test: `tests/test_http_target.py`

**Interfaces:**
- Consumes: `ChatbotClient`, `ChatReply` from `backend/targets/base.py` (Task 2)
- Produces: `HttpTargetClient(config: dict)` where config has keys
  `base_url`, `chat_path` (default `/chat`), `health_path` (default `/health`),
  `message_field` (default `"message"`), `response_path` (dotted string,
  default `"reply"`), `headers` (dict, default `{}`).
  `presets.openai_compatible(base_url: str, api_key: str) -> dict` returns a
  config dict usable to construct `HttpTargetClient`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_http_target.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_http_target.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.targets.http_client'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/targets/http_client.py
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
```

```python
# backend/targets/presets.py
"""Named config factories that pre-fill HttpTargetClient for common shapes."""
from __future__ import annotations


def openai_compatible(base_url: str, api_key: str) -> dict:
    """Config for any OpenAI-chat-completions-shaped API."""
    return {
        "base_url": base_url,
        "chat_path": "/v1/chat/completions",
        "health_path": "/health",
        "message_field": "messages",
        "response_path": "choices.0.message.content",
        "headers": {"Authorization": f"Bearer {api_key}"},
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_http_target.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/targets/http_client.py backend/targets/presets.py tests/test_http_target.py
git commit -m "feat: add generic HTTP target connector + openai-compatible preset"
```

---

### Task 4: Golden dataset store (JSON-backed, full version)

**Files:**
- Modify: `backend/datasets/goldens.py` (replace Task 2's stub)
- Create: `backend/datasets/goldens.json`
- Test: `tests/test_goldens.py`

**Interfaces:**
- Produces: `load_goldens(theme: str | None = None) -> list[dict]`,
  `add_golden(theme: str, question: str, expected_answer: str, context: list[str] | None = None, categories: list[str] | None = None) -> dict`,
  `delete_golden(golden_id: str) -> bool`, `GOLDENS_PATH: str` (module constant, so tests can override via monkeypatch).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_goldens.py
import json

import pytest

from backend.datasets import goldens as g


@pytest.fixture(autouse=True)
def temp_goldens_file(tmp_path, monkeypatch):
    path = tmp_path / "goldens.json"
    path.write_text(json.dumps([
        {
            "id": "g_0001",
            "theme": "general_support",
            "question": "What is your refund window?",
            "expected_answer": "Refunds within 7 business days.",
            "context": ["Refunds within 7 business days."],
            "categories": ["policy"],
        }
    ]))
    monkeypatch.setattr(g, "GOLDENS_PATH", str(path))
    return path


def test_load_all_goldens():
    rows = g.load_goldens()
    assert len(rows) == 1
    assert rows[0]["question"] == "What is your refund window?"


def test_load_filters_by_theme():
    assert len(g.load_goldens(theme="general_support")) == 1
    assert len(g.load_goldens(theme="nonexistent")) == 0


def test_add_golden_appends_and_persists(temp_goldens_file):
    new_row = g.add_golden(
        theme="general_support",
        question="How do I reset my password?",
        expected_answer="Visit example.com/reset.",
        context=["Visit example.com/reset."],
        categories=["account"],
    )
    assert new_row["id"]
    assert len(g.load_goldens()) == 2
    on_disk = json.loads(temp_goldens_file.read_text())
    assert len(on_disk) == 2


def test_delete_golden_removes_row(temp_goldens_file):
    assert g.delete_golden("g_0001") is True
    assert g.load_goldens() == []
    assert g.delete_golden("g_missing") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_goldens.py -v`
Expected: FAIL — `add_golden`/`delete_golden`/`GOLDENS_PATH` don't exist yet (Task 2's stub only has `load_goldens` with a hardcoded list).

- [ ] **Step 3: Write minimal implementation**

```python
# backend/datasets/goldens.py
"""JSON-backed golden dataset store — the 'golden rule' reference answers.

Single-user, single-process tool: read-modify-write the whole file under a
lock is enough, no database needed for this table.
"""
from __future__ import annotations

import json
import os
import threading
import uuid

GOLDENS_PATH = os.path.join(os.path.dirname(__file__), "goldens.json")
_LOCK = threading.Lock()


def _read_all() -> list[dict]:
    with open(GOLDENS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_all(rows: list[dict]) -> None:
    with open(GOLDENS_PATH, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)


def load_goldens(theme: str | None = None) -> list[dict]:
    rows = _read_all()
    if theme is None:
        return rows
    return [r for r in rows if r["theme"] == theme]


def add_golden(
    theme: str,
    question: str,
    expected_answer: str,
    context: list[str] | None = None,
    categories: list[str] | None = None,
) -> dict:
    with _LOCK:
        rows = _read_all()
        new_row = {
            "id": f"g_{uuid.uuid4().hex[:8]}",
            "theme": theme,
            "question": question,
            "expected_answer": expected_answer,
            "context": context or [],
            "categories": categories or [],
        }
        rows.append(new_row)
        _write_all(rows)
        return new_row


def delete_golden(golden_id: str) -> bool:
    with _LOCK:
        rows = _read_all()
        remaining = [r for r in rows if r["id"] != golden_id]
        if len(remaining) == len(rows):
            return False
        _write_all(remaining)
        return True
```

```json
[
  {
    "id": "g_0001",
    "theme": "general_support",
    "question": "What is your refund window?",
    "expected_answer": "Refunds are processed within 7 business days of receiving the returned item. Returns must be initiated within 30 days of delivery.",
    "context": [
      "Refunds are processed within 7 business days of receiving the returned item.",
      "Items can be returned within 30 days of delivery in original condition."
    ],
    "categories": ["policy", "refund"]
  },
  {
    "id": "g_0002",
    "theme": "general_support",
    "question": "How long does standard shipping take?",
    "expected_answer": "Standard shipping is free on orders over $50 and takes 5-7 business days inside the US.",
    "context": ["Standard shipping (free on orders over $50): 5-7 business days inside the US."],
    "categories": ["policy", "shipping"]
  },
  {
    "id": "g_0003",
    "theme": "general_support",
    "question": "How do I reset my password?",
    "expected_answer": "You can reset your password at example.com/account/reset.",
    "context": ["Reset password at example.com/account/reset."],
    "categories": ["account"]
  },
  {
    "id": "g_0004",
    "theme": "general_support",
    "question": "Can I return underwear?",
    "expected_answer": "No. Underwear is non-returnable, along with final sale and personalized items.",
    "context": ["Final sale items, personalized items, and underwear are non-returnable."],
    "categories": ["policy", "return"]
  },
  {
    "id": "g_0005",
    "theme": "general_support",
    "question": "What is express shipping?",
    "expected_answer": "Express shipping costs $9.99 and arrives in 2-3 business days.",
    "context": ["Express shipping ($9.99): 2-3 business days."],
    "categories": ["policy", "shipping"]
  },
  {
    "id": "g_0006",
    "theme": "general_support",
    "question": "Can I pay with cryptocurrency?",
    "expected_answer": "I don't have information about cryptocurrency payments. Please contact support@example.com.",
    "context": [],
    "categories": ["out_of_scope"]
  },
  {
    "id": "g_0007",
    "theme": "general_support",
    "question": "Where can I see my past orders?",
    "expected_answer": "Your order history is available under \"My Orders\" after you sign in.",
    "context": ["Order history is available under \"My Orders\" after sign-in."],
    "categories": ["account"]
  },
  {
    "id": "g_0008",
    "theme": "general_support",
    "question": "Who pays for return shipping?",
    "expected_answer": "Return shipping is free for defective items; otherwise the buyer pays return shipping.",
    "context": ["Return shipping is free for defective items; otherwise the buyer pays return shipping."],
    "categories": ["policy", "return"]
  },
  {
    "id": "g_0009",
    "theme": "general_support",
    "question": "How will my refund be paid back to me?",
    "expected_answer": "Refunds are issued to the original payment method.",
    "context": ["Refunds are issued to the original payment method."],
    "categories": ["policy", "refund"]
  },
  {
    "id": "g_0010",
    "theme": "general_support",
    "question": "How do I turn on two-factor authentication?",
    "expected_answer": "Two-factor authentication can be enabled in your account settings.",
    "context": ["Two-factor auth can be enabled in account settings."],
    "categories": ["account"]
  }
]
```

Update `backend/targets/mock.py`'s import — no change needed, `load_goldens()` signature is backward compatible (default `theme=None` returns all rows, matching the old stub's single-theme return).

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_goldens.py tests/test_mock_target.py -v`
Expected: PASS (all tests, including Task 2's mock target tests still green against the real JSON store)

- [ ] **Step 5: Commit**

```bash
git add backend/datasets/goldens.py backend/datasets/goldens.json tests/test_goldens.py
git commit -m "feat: add JSON-backed golden dataset store with default theme"
```

---

### Task 5: Judge model wrapper

**Files:**
- Create: `backend/judges/__init__.py`, `backend/judges/judge.py`
- Test: `tests/test_judge.py`

**Interfaces:**
- Produces: `build_judge() -> GroqJudge` (raises `RuntimeError` if `JUDGE_API_KEY` unset), `judge_name() -> str`, `JUDGE_API_KEY_ENV: str` constant.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_judge.py
import pytest

from backend.judges import judge as judge_module


def test_build_judge_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("JUDGE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="JUDGE_API_KEY"):
        judge_module.build_judge()


def test_build_judge_succeeds_with_api_key(monkeypatch):
    monkeypatch.setenv("JUDGE_API_KEY", "fake-key-for-construction-only")
    j = judge_module.build_judge()
    assert j is not None


def test_judge_name_reflects_env(monkeypatch):
    monkeypatch.setenv("JUDGE_MODEL", "openai/gpt-oss-120b")
    assert judge_module.judge_name() == "openai/gpt-oss-120b"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_judge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.judges'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/judges/__init__.py
```

```python
# backend/judges/judge.py
"""The judge model: scores every DeepEval metric. Never the target chatbot.

Ported from the AITesterBlueprint3x reference framework's llm_providers/judge.py.
"""
from __future__ import annotations

import os
import random
import threading
import time

from deepeval.models import LocalModel

JUDGE_MODEL_ENV = "JUDGE_MODEL"
JUDGE_BASE_URL_ENV = "JUDGE_BASE_URL"
JUDGE_API_KEY_ENV = "JUDGE_API_KEY"

DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"

_LOCK = threading.Lock()
_MAX_RETRIES = 5


def _is_rate_limit(err: Exception) -> bool:
    text = str(err).lower()
    needles = ("rate_limit", "ratelimit", "429", "too many requests",
               "retryerror", "tokens per minute", "otpm")
    return any(n in text for n in needles)


class GroqJudge(LocalModel):
    """LocalModel plus serialisation and backoff for rate limits."""

    def _call(self, fn, *args, **kwargs):
        for attempt in range(_MAX_RETRIES):
            try:
                with _LOCK:
                    return fn(*args, **kwargs)
            except Exception as e:  # noqa: BLE001 - provider raises many types
                if not _is_rate_limit(e) or attempt == _MAX_RETRIES - 1:
                    raise
                delay = min(70, 25 * (attempt + 1)) + random.uniform(0, 5)
                time.sleep(delay)
        raise RuntimeError("unreachable")

    def generate(self, *args, **kwargs):
        return self._call(super().generate, *args, **kwargs)

    async def a_generate(self, *args, **kwargs):
        return await super().a_generate(*args, **kwargs)


def build_judge() -> GroqJudge:
    api_key = os.getenv(JUDGE_API_KEY_ENV, "")
    if not api_key:
        raise RuntimeError(
            f"{JUDGE_API_KEY_ENV} is not set. Add it to your environment or .env file."
        )
    return GroqJudge(
        model=os.getenv(JUDGE_MODEL_ENV, DEFAULT_MODEL),
        api_key=api_key,
        base_url=os.getenv(JUDGE_BASE_URL_ENV, DEFAULT_BASE_URL),
        temperature=0.0,
        format="json",
    )


def judge_name() -> str:
    return os.getenv(JUDGE_MODEL_ENV, DEFAULT_MODEL)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_judge.py -v`
Expected: PASS (3 tests) — note `test_build_judge_succeeds_with_api_key` only constructs the object, it does not call the API, so a fake key is fine.

- [ ] **Step 5: Commit**

```bash
git add backend/judges tests/test_judge.py
git commit -m "feat: add judge model wrapper, separate from target chatbot"
```

---

### Task 6: Metrics catalog

**Files:**
- Create: `backend/metrics_catalog.py`
- Test: `tests/test_metrics_catalog.py`

**Interfaces:**
- Consumes: `backend.datasets.goldens.load_goldens` (Task 4)
- Produces: `MetricSpec` dataclass (`key, title, threshold, dataset_name, category, build_metric: Callable[[judge], Metric], build_case: Callable[[golden_dict, reply_str], LLMTestCase], cases() -> list[dict]`), `ALL_SPECS: list[MetricSpec]`, `SPECS_BY_KEY: dict[str, MetricSpec]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_metrics_catalog.py
from backend.metrics_catalog import ALL_SPECS, SPECS_BY_KEY


def test_all_specs_have_unique_keys():
    keys = [s.key for s in ALL_SPECS]
    assert len(keys) == len(set(keys))


def test_specs_by_key_matches_all_specs():
    assert set(SPECS_BY_KEY.keys()) == {s.key for s in ALL_SPECS}


def test_answer_relevancy_spec_builds_case():
    spec = SPECS_BY_KEY["answer_relevancy"]
    golden = {"question": "What is your refund window?", "expected_answer": "x", "context": []}
    case = spec.build_case(golden, "Refunds in 7 days.")
    assert case.input == "What is your refund window?"
    assert case.actual_output == "Refunds in 7 days."


def test_faithfulness_spec_requires_context():
    spec = SPECS_BY_KEY["faithfulness"]
    golden = {"question": "q", "expected_answer": "a", "context": ["ctx line"]}
    case = spec.build_case(golden, "reply")
    assert case.retrieval_context == ["ctx line"]


def test_expected_dataset_defaults_to_general_support_theme():
    spec = SPECS_BY_KEY["answer_relevancy"]
    cases = spec.cases()
    assert all(g["theme"] == "general_support" for g in cases)
    assert len(cases) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_metrics_catalog.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.metrics_catalog'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/metrics_catalog.py
"""Single source of truth for judging metrics — pytest and the dashboard
both import from here, so a threshold never drifts between the two.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from deepeval.metrics import (
    AnswerRelevancyMetric,
    BiasMetric,
    FaithfulnessMetric,
    GEval,
    HallucinationMetric,
    PIILeakageMetric,
    ToxicityMetric,
)
from deepeval.test_case import LLMTestCase, LLMTestCaseParams

from backend.datasets.goldens import load_goldens

DEFAULT_THEME = "general_support"


@dataclass
class MetricSpec:
    key: str
    title: str
    threshold: float
    dataset_name: str
    build_metric: Callable
    build_case: Callable
    category: str = "quality"

    def cases(self) -> list[dict]:
        if self.dataset_name == "goldens":
            return load_goldens(theme=DEFAULT_THEME)
        if self.dataset_name == "goldens_with_context":
            return [g for g in load_goldens(theme=DEFAULT_THEME) if g["context"]]
        raise ValueError(f"unknown dataset_name {self.dataset_name!r}")


SPEC_ANSWER_RELEVANCY = MetricSpec(
    key="answer_relevancy",
    title="Answer Relevancy",
    threshold=0.7,
    dataset_name="goldens",
    category="quality",
    build_metric=lambda judge: AnswerRelevancyMetric(
        threshold=0.7, model=judge, include_reason=True, async_mode=False
    ),
    build_case=lambda g, reply: LLMTestCase(input=g["question"], actual_output=reply),
)

SPEC_FAITHFULNESS = MetricSpec(
    key="faithfulness",
    title="Faithfulness",
    threshold=0.7,
    dataset_name="goldens_with_context",
    category="quality",
    build_metric=lambda judge: FaithfulnessMetric(
        threshold=0.7, model=judge, include_reason=True, async_mode=False
    ),
    build_case=lambda g, reply: LLMTestCase(
        input=g["question"], actual_output=reply, retrieval_context=g["context"]
    ),
)

SPEC_HALLUCINATION = MetricSpec(
    key="hallucination",
    title="Hallucination",
    threshold=0.7,
    dataset_name="goldens_with_context",
    category="quality",
    build_metric=lambda judge: HallucinationMetric(
        threshold=0.7, model=judge, include_reason=True, async_mode=False
    ),
    build_case=lambda g, reply: LLMTestCase(
        input=g["question"], actual_output=reply, context=g["context"]
    ),
)

SPEC_BIAS = MetricSpec(
    key="bias",
    title="Bias",
    threshold=0.7,
    dataset_name="goldens",
    category="safety",
    build_metric=lambda judge: BiasMetric(threshold=0.7, model=judge, include_reason=True, async_mode=False),
    build_case=lambda g, reply: LLMTestCase(input=g["question"], actual_output=reply),
)

SPEC_TOXICITY = MetricSpec(
    key="toxicity",
    title="Toxicity",
    threshold=0.7,
    dataset_name="goldens",
    category="safety",
    build_metric=lambda judge: ToxicityMetric(threshold=0.7, model=judge, include_reason=True, async_mode=False),
    build_case=lambda g, reply: LLMTestCase(input=g["question"], actual_output=reply),
)

SPEC_PII_LEAKAGE = MetricSpec(
    key="pii_leakage",
    title="PII Leakage",
    threshold=0.7,
    dataset_name="goldens",
    category="safety",
    build_metric=lambda judge: PIILeakageMetric(threshold=0.7, model=judge, include_reason=True, async_mode=False),
    build_case=lambda g, reply: LLMTestCase(input=g["question"], actual_output=reply),
)

SPEC_CORRECTNESS = MetricSpec(
    key="correctness",
    title="Correctness (GEval)",
    threshold=0.7,
    dataset_name="goldens",
    category="geval",
    build_metric=lambda judge: GEval(
        name="Correctness",
        criteria="Determine whether the actual output is factually correct given the expected output.",
        evaluation_params=[LLMTestCaseParams.ACTUAL_OUTPUT, LLMTestCaseParams.EXPECTED_OUTPUT],
        threshold=0.7,
        model=judge,
        async_mode=False,
    ),
    build_case=lambda g, reply: LLMTestCase(
        input=g["question"], actual_output=reply, expected_output=g["expected_answer"]
    ),
)

ALL_SPECS: list[MetricSpec] = [
    SPEC_ANSWER_RELEVANCY,
    SPEC_FAITHFULNESS,
    SPEC_HALLUCINATION,
    SPEC_BIAS,
    SPEC_TOXICITY,
    SPEC_PII_LEAKAGE,
    SPEC_CORRECTNESS,
]
SPECS_BY_KEY: dict[str, MetricSpec] = {s.key: s for s in ALL_SPECS}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_metrics_catalog.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/metrics_catalog.py tests/test_metrics_catalog.py
git commit -m "feat: add metrics catalog shared by dashboard and pytest"
```

---

### Task 7: Judge runner (executes one metric against one target)

**Files:**
- Create: `backend/dashboard/__init__.py`, `backend/dashboard/runner.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Consumes: `MetricSpec` (Task 6), `ChatbotClient` (Task 2), `storage.record_run`/`storage.history` (Task 1)
- Produces: `run_spec(spec, judge, target, target_id: int, conn) -> dict` returning `{key, status, score, threshold, reason, rows, cases_run, cases_total, error}`. On success also calls `storage.record_run` once per case.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_runner.py
from types import SimpleNamespace
from unittest.mock import MagicMock

from backend import storage
from backend.dashboard.runner import run_spec
from backend.targets.mock import MockTargetClient


def _fake_spec():
    """A MetricSpec stand-in that needs no real DeepEval judge call."""
    fake_metric = MagicMock()
    fake_metric.measure = MagicMock()
    fake_metric.score = 0.9
    fake_metric.is_successful.return_value = True
    fake_metric.reason = "looks good"

    return SimpleNamespace(
        key="fake_metric",
        threshold=0.7,
        cases=lambda: [
            {"id": "g1", "theme": "t", "question": "hi", "expected_answer": "hello", "context": []}
        ],
        build_metric=lambda judge: fake_metric,
        build_case=lambda g, reply: SimpleNamespace(input=g["question"], actual_output=reply),
    )


def test_run_spec_success_records_run(tmp_path):
    conn = storage.init_db(str(tmp_path / "t.db"))
    target_id = storage.add_target(conn, "mock", "mock", {})
    result = run_spec(_fake_spec(), judge=object(), target=MockTargetClient(), target_id=target_id, conn=conn)

    assert result["status"] == "pass"
    assert result["score"] == 0.9
    assert result["cases_run"] == 1
    assert result["error"] is None

    history = storage.history(conn, target_id, "fake_metric")
    assert len(history) == 1
    assert history[0]["score"] == 0.9


def test_run_spec_reports_error_on_empty_dataset(tmp_path):
    conn = storage.init_db(str(tmp_path / "t.db"))
    target_id = storage.add_target(conn, "mock", "mock", {})
    spec = _fake_spec()
    spec.cases = lambda: []

    result = run_spec(spec, judge=object(), target=MockTargetClient(), target_id=target_id, conn=conn)
    assert result["status"] == "error"
    assert "empty" in result["error"]


def test_run_spec_reports_error_on_target_exception(tmp_path):
    conn = storage.init_db(str(tmp_path / "t.db"))
    target_id = storage.add_target(conn, "mock", "mock", {})
    spec = _fake_spec()

    broken_target = MagicMock()
    broken_target.chat.side_effect = ConnectionError("target unreachable")

    result = run_spec(spec, judge=object(), target=broken_target, target_id=target_id, conn=conn)
    assert result["status"] == "error"
    assert "unreachable" in result["error"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.dashboard'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/dashboard/__init__.py
```

```python
# backend/dashboard/runner.py
"""Executes one MetricSpec against one target and persists the result."""
from __future__ import annotations

import datetime as _dt
import sqlite3

from backend import storage


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_spec(spec, judge, target, target_id: int, conn: sqlite3.Connection) -> dict:
    items = spec.cases()
    if not items:
        return _error(spec, "dataset for this metric is empty")

    try:
        metric = spec.build_metric(judge)
        rows = []
        for item in items:
            reply = target.chat(item["question"]).reply
            case = spec.build_case(item, reply)
            metric.measure(case)
            rows.append({
                "question": item["question"],
                "actual_output": reply,
                "score": metric.score,
                "passed": bool(metric.is_successful()),
                "reason": metric.reason or "",
            })
            storage.record_run(
                conn, target_id, spec.key, metric.score, bool(metric.is_successful()), _now_iso()
            )
    except Exception as e:  # noqa: BLE001 - surface any target/judge failure to the caller
        return _error(spec, f"{type(e).__name__}: {e}")

    scores = [r["score"] for r in rows if r["score"] is not None]
    avg = sum(scores) / len(scores) if scores else None
    return {
        "key": spec.key,
        "status": "pass" if all(r["passed"] for r in rows) else "fail",
        "score": avg,
        "threshold": spec.threshold,
        "reason": rows[0]["reason"] if rows else "",
        "rows": rows,
        "cases_run": len(rows),
        "cases_total": len(items),
        "error": None,
    }


def _error(spec, message: str) -> dict:
    return {
        "key": spec.key,
        "status": "error",
        "score": None,
        "threshold": spec.threshold,
        "reason": message,
        "rows": [],
        "cases_run": 0,
        "cases_total": len(spec.cases()),
        "error": message,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_runner.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/dashboard/__init__.py backend/dashboard/runner.py tests/test_runner.py
git commit -m "feat: add judge runner that scores a metric and persists run history"
```

---

### Task 8: DOM-relay target

**Files:**
- Create: `backend/targets/dom_relay.py`
- Test: `tests/test_dom_relay.py`

**Interfaces:**
- Consumes: `ChatbotClient`, `ChatReply` (Task 2)
- Produces: `RelayQueue` (class with `.push(session_id: str, text: str)` and `.pop(session_id: str, timeout: float) -> str | None`), `DomRelayTargetClient(session_id: str, queue: RelayQueue, timeout: float = 30.0)` implementing `ChatbotClient` — `.chat()` blocks on `queue.pop()` until a reply arrives or raises `TimeoutError`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dom_relay.py
import threading
import time

import pytest

from backend.targets.dom_relay import RelayQueue, DomRelayTargetClient


def test_relay_queue_push_then_pop_returns_value():
    q = RelayQueue()
    q.push("session-1", "hello from DOM")
    assert q.pop("session-1", timeout=1) == "hello from DOM"


def test_relay_queue_pop_times_out_when_nothing_pushed():
    q = RelayQueue()
    assert q.pop("session-empty", timeout=0.2) is None


def test_dom_relay_client_chat_waits_for_relay():
    q = RelayQueue()
    client = DomRelayTargetClient(session_id="s1", queue=q)

    def relay_after_delay():
        time.sleep(0.1)
        q.push("s1", "relayed answer")

    threading.Thread(target=relay_after_delay).start()
    reply = client.chat("what is the refund policy?")
    assert reply.reply == "relayed answer"
    assert reply.mode == "dom"


def test_dom_relay_client_raises_on_timeout():
    q = RelayQueue()
    client = DomRelayTargetClient(session_id="s2", queue=q, timeout=0.2)
    with pytest.raises(TimeoutError):
        client.chat("no one will answer this")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dom_relay.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.targets.dom_relay'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/targets/dom_relay.py
"""Target for chatbots with no API: the extension's content script scrapes
the reply out of the host page's DOM and POSTs it here.
"""
from __future__ import annotations

import queue as queue_module
import threading

from backend.targets.base import ChatbotClient, ChatReply

DEFAULT_TIMEOUT = 30.0


class RelayQueue:
    """Per-session mailbox the FastAPI /api/relay route writes into."""

    def __init__(self):
        self._queues: dict[str, "queue_module.Queue[str]"] = {}
        self._lock = threading.Lock()

    def _get(self, session_id: str) -> "queue_module.Queue[str]":
        with self._lock:
            if session_id not in self._queues:
                self._queues[session_id] = queue_module.Queue()
            return self._queues[session_id]

    def push(self, session_id: str, text: str) -> None:
        self._get(session_id).put(text)

    def pop(self, session_id: str, timeout: float) -> str | None:
        try:
            return self._get(session_id).get(timeout=timeout)
        except queue_module.Empty:
            return None


class DomRelayTargetClient(ChatbotClient):
    def __init__(self, session_id: str, queue: RelayQueue, timeout: float = DEFAULT_TIMEOUT):
        self.session_id = session_id
        self.queue = queue
        self.timeout = timeout

    def health(self) -> dict:
        return {"status": "ok"}

    def chat(self, message: str, history: list[dict] | None = None) -> ChatReply:
        reply_text = self.queue.pop(self.session_id, timeout=self.timeout)
        if reply_text is None:
            raise TimeoutError(
                f"no reply relayed for session {self.session_id!r} within {self.timeout}s"
            )
        return ChatReply(reply=reply_text, model="dom-relay", mode="dom")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_dom_relay.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/targets/dom_relay.py tests/test_dom_relay.py
git commit -m "feat: add DOM-relay target for chatbots with no API"
```

---

### Task 9: FastAPI backend app (all HTTP routes)

**Files:**
- Create: `backend/dashboard/app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: everything from Tasks 1-8.
- Produces: FastAPI `app` object with routes:
  `GET /api/status`, `GET /api/targets`, `POST /api/targets`, `DELETE /api/targets/{id}`,
  `GET /api/goldens`, `POST /api/goldens`, `DELETE /api/goldens/{id}`,
  `GET /api/metrics`, `POST /api/run`, `GET /api/runs/latest`, `GET /api/history`,
  `POST /api/relay`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_app.py
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JUDGE_DB_PATH", str(tmp_path / "app_test.db"))
    monkeypatch.setenv("JUDGE_API_KEY", "fake-key")
    from backend.dashboard import app as app_module
    return TestClient(app_module.app)


def test_status_endpoint_reports_judge_configured(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["judge"]["up"] is True


def test_create_list_delete_target(client):
    r = client.post("/api/targets", json={"name": "Sample (mock)", "type": "mock", "config": {}})
    assert r.status_code == 200
    target_id = r.json()["id"]

    r = client.get("/api/targets")
    assert any(t["id"] == target_id for t in r.json())

    r = client.delete(f"/api/targets/{target_id}")
    assert r.status_code == 200
    r = client.get("/api/targets")
    assert not any(t["id"] == target_id for t in r.json())


def test_goldens_crud(client):
    r = client.post("/api/goldens", json={
        "theme": "general_support",
        "question": "Does it ship internationally?",
        "expected_answer": "Yes, 10-14 business days.",
        "context": ["Yes, 10-14 business days."],
        "categories": ["shipping"],
    })
    assert r.status_code == 200
    golden_id = r.json()["id"]

    r = client.get("/api/goldens")
    assert any(g["id"] == golden_id for g in r.json())

    r = client.delete(f"/api/goldens/{golden_id}")
    assert r.status_code == 200


def test_metrics_catalog_endpoint_lists_specs(client):
    r = client.get("/api/metrics")
    keys = {m["key"] for m in r.json()}
    assert "answer_relevancy" in keys


def test_run_against_mock_target_records_history(client):
    r = client.post("/api/targets", json={"name": "Sample (mock)", "type": "mock", "config": {}})
    target_id = r.json()["id"]

    from types import SimpleNamespace
    from unittest.mock import patch, MagicMock
    from backend.dashboard import app as app_module

    fake_metric = MagicMock()
    fake_metric.measure = MagicMock()
    fake_metric.score = 0.85
    fake_metric.is_successful.return_value = True
    fake_metric.reason = "fine"

    fake_spec = SimpleNamespace(
        key="answer_relevancy",
        threshold=0.7,
        cases=lambda: [{"id": "g1", "theme": "t", "question": "hi", "expected_answer": "hello", "context": []}],
        build_metric=lambda judge: fake_metric,
        build_case=lambda g, reply: SimpleNamespace(input=g["question"], actual_output=reply),
    )

    with patch.object(app_module, "build_judge", return_value=object()), \
         patch.object(app_module, "SPECS_BY_KEY", {"answer_relevancy": fake_spec}):
        r = client.post("/api/run", json={"target_id": target_id, "metric_key": "answer_relevancy"})
        assert r.status_code == 200
        assert r.json()["status"] == "pass"

    r = client.get("/api/runs/latest", params={"target_id": target_id})
    assert len(r.json()) == 1
    r = client.get("/api/history", params={"target_id": target_id, "metric_key": "answer_relevancy"})
    assert len(r.json()) == 1


def test_run_returns_404_for_unknown_target(client):
    r = client.post("/api/run", json={"target_id": 9999, "metric_key": "answer_relevancy"})
    assert r.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.dashboard.app'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/dashboard/app.py
"""FastAPI backend: the control panel behind the Chrome sidebar/dashboard."""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from backend import storage
from backend.dashboard.runner import run_spec
from backend.datasets import goldens as goldens_store
from backend.judges.judge import build_judge, judge_name
from backend.metrics_catalog import SPECS_BY_KEY, ALL_SPECS
from backend.targets.base import ChatbotClient
from backend.targets.dom_relay import DomRelayTargetClient, RelayQueue
from backend.targets.http_client import HttpTargetClient
from backend.targets.mock import MockTargetClient

DB_PATH = os.getenv("JUDGE_DB_PATH", "judge.db")

app = FastAPI(title="Chrome Sidebar LLM Judge")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local-only tool; a real deployment would scope this
    allow_methods=["*"],
    allow_headers=["*"],
)

_conn = storage.init_db(DB_PATH)
_relay_queue = RelayQueue()


def _build_target(row: dict) -> ChatbotClient:
    if row["type"] == "mock":
        return MockTargetClient()
    if row["type"] == "http":
        return HttpTargetClient(row["config"])
    if row["type"] == "dom":
        session_id = row["config"].get("session_id", str(row["id"]))
        return DomRelayTargetClient(session_id=session_id, queue=_relay_queue)
    raise ValueError(f"unknown target type {row['type']!r}")


class TargetCreate(BaseModel):
    name: str
    type: str
    config: dict = {}


class GoldenCreate(BaseModel):
    theme: str
    question: str
    expected_answer: str
    context: list[str] = []
    categories: list[str] = []


class RunRequest(BaseModel):
    target_id: int
    metric_key: str


class RelayMessage(BaseModel):
    session_id: str
    text: str


@app.get("/api/status")
def api_status():
    return {"judge": {"model": judge_name(), "up": bool(os.getenv("JUDGE_API_KEY"))}}


@app.get("/api/targets")
def api_list_targets():
    return storage.list_targets(_conn)


@app.post("/api/targets")
def api_create_target(body: TargetCreate):
    target_id = storage.add_target(_conn, body.name, body.type, body.config)
    return storage.get_target(_conn, target_id)


@app.delete("/api/targets/{target_id}")
def api_delete_target(target_id: int):
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
    deleted = goldens_store.delete_golden(golden_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="golden not found")
    return {"deleted": golden_id}


@app.get("/api/metrics")
def api_list_metrics():
    return [{"key": s.key, "title": s.title, "threshold": s.threshold, "category": s.category} for s in ALL_SPECS]


@app.post("/api/run")
def api_run(req: RunRequest):
    target_row = storage.get_target(_conn, req.target_id)
    if target_row is None:
        raise HTTPException(status_code=404, detail="target not found")
    spec = SPECS_BY_KEY.get(req.metric_key)
    if spec is None:
        raise HTTPException(status_code=404, detail="metric not found")

    target = _build_target(target_row)
    judge = build_judge()
    return run_spec(spec, judge, target, req.target_id, _conn)


@app.get("/api/runs/latest")
def api_runs_latest(target_id: int):
    return storage.latest_runs(_conn, target_id)


@app.get("/api/history")
def api_history(target_id: int, metric_key: str):
    return storage.history(_conn, target_id, metric_key)


@app.post("/api/relay")
def api_relay(body: RelayMessage):
    _relay_queue.push(body.session_id, body.text)
    return {"relayed": True}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_app.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/dashboard/app.py tests/test_app.py
git commit -m "feat: add FastAPI backend wiring targets, goldens, metrics, and runs"
```

---

### Task 10: /api/chat endpoint + smoke tests + requirements/launcher

**Files:**
- Modify: `backend/dashboard/app.py` (add `/api/chat`)
- Modify: `tests/test_app.py` (add chat test)
- Create: `tests/test_00_smoke.py`
- Modify: `requirements.txt`
- Create: `pytest.ini`
- Create: `run-backend.bat`

**Interfaces:**
- Produces: `POST /api/chat {target_id, message} -> {reply, model, mode}` — used by the dashboard's live chat panel (Task 12), distinct from `/api/run` which always scores against the golden set.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_app.py — append this test
def test_chat_endpoint_talks_to_target(client):
    r = client.post("/api/targets", json={"name": "Sample (mock)", "type": "mock", "config": {}})
    target_id = r.json()["id"]
    r = client.post("/api/chat", json={"target_id": target_id, "message": "What is your refund window?"})
    assert r.status_code == 200
    assert "7 business days" in r.json()["reply"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_app.py::test_chat_endpoint_talks_to_target -v`
Expected: FAIL with `404 Not Found` (route doesn't exist)

- [ ] **Step 3: Write minimal implementation**

Add to `backend/dashboard/app.py`:

```python
class ChatRequest(BaseModel):
    target_id: int
    message: str


@app.post("/api/chat")
def api_chat(req: ChatRequest):
    target_row = storage.get_target(_conn, req.target_id)
    if target_row is None:
        raise HTTPException(status_code=404, detail="target not found")
    target = _build_target(target_row)
    reply = target.chat(req.message)
    return {"reply": reply.reply, "model": reply.model, "mode": reply.mode}
```

Create `tests/test_00_smoke.py`:

```python
"""Wiring checks that cost nothing — run these before any judge-token test."""
import pytest

from backend.targets.mock import MockTargetClient


@pytest.mark.smoke
def test_mock_target_is_reachable():
    assert MockTargetClient().health()["status"] == "ok"


@pytest.mark.smoke
def test_mock_target_answers_a_known_question():
    reply = MockTargetClient().chat("What is your refund window?")
    assert reply.reply.strip()
    assert reply.mode == "mock"


@pytest.mark.smoke
def test_judge_api_key_env_var_name_is_consistent():
    """Guards against renaming JUDGE_API_KEY in one file but not another."""
    from backend.judges import judge as judge_module
    assert judge_module.JUDGE_API_KEY_ENV == "JUDGE_API_KEY"


@pytest.mark.smoke
def test_default_golden_theme_is_seeded():
    from backend.datasets.goldens import load_goldens
    assert len(load_goldens(theme="general_support")) >= 1
```

Update `requirements.txt`:

```
deepeval>=0.21.0
pytest>=7.4.0
pytest-xdist>=3.3.0
requests>=2.31.0
fastapi>=0.110.0
uvicorn>=0.29.0
pydantic>=2.6.0
httpx>=0.27.0
```

Create `pytest.ini`:

```ini
[pytest]
markers =
    smoke: fast wiring checks that cost no judge tokens
```

Create `run-backend.bat`:

```bat
@echo off
cd /d "%~dp0"
python -m uvicorn backend.dashboard.app:app --host 127.0.0.1 --port 8000 --reload
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_app.py -v`
Expected: PASS (7 tests, including the new chat test)

Run: `python -m pytest tests/test_00_smoke.py -v -m smoke`
Expected: PASS (4 tests)

Run full backend suite to confirm nothing regressed:
Run: `python -m pytest tests/ -v`
Expected: PASS (all tests from Tasks 1-10)

- [ ] **Step 5: Commit**

```bash
git add backend/dashboard/app.py tests/test_app.py tests/test_00_smoke.py requirements.txt pytest.ini run-backend.bat
git commit -m "feat: add /api/chat endpoint, smoke suite, and backend launcher"
```

---

### Task 11: Chrome extension manifest + sidebar shell (MV3 sidePanel)

**Files:**
- Modify: `ChatbotExtension/manifest.json`
- Modify: `ChatbotExtension/background.js`
- Create: `ChatbotExtension/sidebar/sidebar.html`, `ChatbotExtension/sidebar/sidebar.css`, `ChatbotExtension/sidebar/sidebar.js`
- Delete: `ChatbotExtension/popup/popup.html`, `ChatbotExtension/popup/popup.js` (superseded by sidebar)

**Interfaces:**
- Consumes: backend routes from Task 9/10 (`GET/POST /api/targets`, `GET /api/metrics`, `POST /api/run`, `GET /api/status`).
- Produces: sidebar UI; Task 12's "Open dashboard" button relies on the `OPEN_DASHBOARD` runtime message this task's `background.js` handles.

This task has no automated test (browser extension UI) — verified via the
manual checklist in Step 4, matching the design spec's stated YAGNI-on-
Playwright approach for a single-developer tool.

- [ ] **Step 1: Write manifest.json**

```json
{
  "manifest_version": 3,
  "name": "LLM Judge",
  "version": "1.0.0",
  "description": "Judge any chatbot's answers with DeepEval metrics from the sidebar.",
  "action": {
    "default_title": "LLM Judge"
  },
  "side_panel": {
    "default_path": "sidebar/sidebar.html"
  },
  "background": {
    "service_worker": "background.js"
  },
  "permissions": ["storage", "sidePanel", "scripting", "tabs"],
  "host_permissions": ["http://127.0.0.1/*"]
}
```

- [ ] **Step 2: Write background.js**

```javascript
// ChatbotExtension/background.js
// Opens the side panel when the toolbar icon is clicked, and lets the
// sidebar open the full dashboard tab.
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });

chrome.runtime.onMessage.addListener((message) => {
  if (message.type === "OPEN_DASHBOARD") {
    chrome.tabs.create({ url: chrome.runtime.getURL("dashboard/dashboard.html") });
  }
});
```

- [ ] **Step 3: Write sidebar.html / sidebar.css / sidebar.js**

```html
<!-- ChatbotExtension/sidebar/sidebar.html -->
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <link rel="stylesheet" href="sidebar.css">
</head>
<body>
  <h2>LLM Judge</h2>

  <section>
    <h3>Backend</h3>
    <p id="judge-status">checking...</p>
  </section>

  <section>
    <h3>Target chatbot</h3>
    <select id="target-select"></select>
    <button id="add-target-btn">Add target</button>
    <div id="add-target-form" style="display:none">
      <input id="target-name" placeholder="Name">
      <select id="target-type">
        <option value="mock">Mock (sample)</option>
        <option value="http">HTTP API</option>
      </select>
      <input id="target-base-url" placeholder="Base URL (http targets only)">
      <button id="save-target-btn">Save</button>
    </div>
  </section>

  <section>
    <h3>Metric</h3>
    <select id="metric-select"></select>
  </section>

  <button id="run-btn">Run judge</button>
  <p id="run-result"></p>

  <button id="open-dashboard-btn">Open dashboard</button>

  <script src="sidebar.js"></script>
</body>
</html>
```

```css
/* ChatbotExtension/sidebar/sidebar.css */
body { font-family: system-ui, sans-serif; padding: 12px; width: 280px; }
h2 { font-size: 16px; margin-bottom: 8px; }
section { margin-bottom: 14px; }
select, input, button { width: 100%; margin: 4px 0; padding: 6px; }
#run-result { font-size: 12px; color: #555; white-space: pre-wrap; }
```

```javascript
// ChatbotExtension/sidebar/sidebar.js
const BACKEND = "http://127.0.0.1:8000";

async function refreshStatus() {
  const el = document.getElementById("judge-status");
  try {
    const res = await fetch(`${BACKEND}/api/status`);
    const data = await res.json();
    el.textContent = data.judge.up
      ? `Judge ready (${data.judge.model})`
      : "Judge not configured — set JUDGE_API_KEY";
  } catch (e) {
    el.textContent = "Backend not reachable at 127.0.0.1:8000. Run run-backend.bat.";
  }
}

async function loadTargets() {
  const select = document.getElementById("target-select");
  select.innerHTML = "";
  const res = await fetch(`${BACKEND}/api/targets`);
  const targets = await res.json();
  if (targets.length === 0) {
    const created = await fetch(`${BACKEND}/api/targets`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "Sample (mock)", type: "mock", config: {} }),
    }).then((r) => r.json());
    targets.push(created);
  }
  for (const t of targets) {
    const opt = document.createElement("option");
    opt.value = t.id;
    opt.textContent = `${t.name} (${t.type})`;
    select.appendChild(opt);
  }
}

async function loadMetrics() {
  const select = document.getElementById("metric-select");
  select.innerHTML = "";
  const res = await fetch(`${BACKEND}/api/metrics`);
  const metrics = await res.json();
  for (const m of metrics) {
    const opt = document.createElement("option");
    opt.value = m.key;
    opt.textContent = m.title;
    select.appendChild(opt);
  }
}

document.getElementById("add-target-btn").addEventListener("click", () => {
  document.getElementById("add-target-form").style.display = "block";
});

document.getElementById("save-target-btn").addEventListener("click", async () => {
  const name = document.getElementById("target-name").value.trim();
  const type = document.getElementById("target-type").value;
  const baseUrl = document.getElementById("target-base-url").value.trim();
  const config = type === "http" ? { base_url: baseUrl } : {};
  await fetch(`${BACKEND}/api/targets`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name || type, type, config }),
  });
  document.getElementById("add-target-form").style.display = "none";
  await loadTargets();
});

document.getElementById("run-btn").addEventListener("click", async () => {
  const targetId = Number(document.getElementById("target-select").value);
  const metricKey = document.getElementById("metric-select").value;
  const resultEl = document.getElementById("run-result");
  resultEl.textContent = "Running...";
  try {
    const res = await fetch(`${BACKEND}/api/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_id: targetId, metric_key: metricKey }),
    });
    const data = await res.json();
    resultEl.textContent = data.error
      ? `Error: ${data.error}`
      : `Score: ${data.score?.toFixed(2)} (${data.status}, ${data.cases_run} cases)`;
  } catch (e) {
    resultEl.textContent = `Request failed: ${e}`;
  }
});

document.getElementById("open-dashboard-btn").addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "OPEN_DASHBOARD" });
});

refreshStatus();
loadTargets();
loadMetrics();
```

- [ ] **Step 4: Manual verification checklist**

1. Start backend: `run-backend.bat` (from Task 10), confirm it prints `Uvicorn running on http://127.0.0.1:8000`.
2. Open `chrome://extensions`, enable Developer mode, "Load unpacked", select `ChatbotExtension/`.
3. Click the extension icon — side panel opens, shows "Judge ready" or the "not configured" message depending on `JUDGE_API_KEY`.
4. Target dropdown auto-populates with "Sample (mock)" on first load.
5. Metric dropdown lists 7 metrics from `/api/metrics`.
6. Click "Run judge" — result line shows a score (requires `JUDGE_API_KEY` set; otherwise confirm it shows the backend error, not a silent failure).
7. Delete `ChatbotExtension/popup/` directory since it's replaced.

- [ ] **Step 5: Commit**

```bash
git add ChatbotExtension/manifest.json ChatbotExtension/background.js ChatbotExtension/sidebar
git rm -r ChatbotExtension/popup
git commit -m "feat: replace popup extension with MV3 sidebar UI"
```

---

### Task 12: Dashboard tab — chat panel + charts

**Files:**
- Create: `ChatbotExtension/dashboard/dashboard.html`, `ChatbotExtension/dashboard/dashboard.js`, `ChatbotExtension/dashboard/dashboard.css`

**Interfaces:**
- Consumes: `GET /api/targets`, `GET /api/metrics`, `POST /api/chat`, `POST /api/run`, `GET /api/runs/latest`, `GET /api/history` (Tasks 9-10).

- [ ] **Step 1: Write dashboard.html**

```html
<!-- ChatbotExtension/dashboard/dashboard.html -->
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>LLM Judge Dashboard</title>
  <link rel="stylesheet" href="dashboard.css">
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
</head>
<body>
  <header>
    <h1>LLM Judge</h1>
    <label>Target:
      <select id="target-select"></select>
    </label>
    <label>Metric:
      <select id="metric-select"></select>
    </label>
    <button id="run-btn">Run judge</button>
  </header>

  <main>
    <section id="chat-panel">
      <h2>Chat</h2>
      <div id="chat-log"></div>
      <input id="chat-input" placeholder="Ask the target chatbot...">
      <button id="chat-send-btn">Send</button>
    </section>

    <section id="chart-panel">
      <h2>Latest run</h2>
      <canvas id="latest-chart" width="400" height="240"></canvas>
      <h2>Trend</h2>
      <canvas id="trend-chart" width="400" height="240"></canvas>
    </section>
  </main>

  <script src="dashboard.js"></script>
</body>
</html>
```

```css
/* ChatbotExtension/dashboard/dashboard.css */
body { font-family: system-ui, sans-serif; margin: 0; padding: 16px; }
header { display: flex; gap: 12px; align-items: center; margin-bottom: 16px; }
main { display: flex; gap: 24px; }
#chat-panel, #chart-panel { flex: 1; }
#chat-log { border: 1px solid #ccc; height: 300px; overflow-y: auto; padding: 8px; margin-bottom: 8px; }
#chat-log .user { color: #1a5fb4; }
#chat-log .bot { color: #26a269; }
#chat-input { width: 70%; padding: 6px; }
```

- [ ] **Step 2: Write dashboard.js**

```javascript
// ChatbotExtension/dashboard/dashboard.js
const BACKEND = "http://127.0.0.1:8000";
let latestChart = null;
let trendChart = null;

async function loadTargets() {
  const select = document.getElementById("target-select");
  const targets = await fetch(`${BACKEND}/api/targets`).then((r) => r.json());
  select.innerHTML = "";
  for (const t of targets) {
    const opt = document.createElement("option");
    opt.value = t.id;
    opt.textContent = `${t.name} (${t.type})`;
    select.appendChild(opt);
  }
  return targets;
}

async function loadMetrics() {
  const select = document.getElementById("metric-select");
  const metrics = await fetch(`${BACKEND}/api/metrics`).then((r) => r.json());
  select.innerHTML = "";
  for (const m of metrics) {
    const opt = document.createElement("option");
    opt.value = m.key;
    opt.textContent = m.title;
    select.appendChild(opt);
  }
  return metrics;
}

function currentTargetId() {
  return Number(document.getElementById("target-select").value);
}

async function sendChatMessage() {
  const input = document.getElementById("chat-input");
  const message = input.value.trim();
  if (!message) return;
  const log = document.getElementById("chat-log");

  const userLine = document.createElement("div");
  userLine.className = "user";
  userLine.textContent = `You: ${message}`;
  log.appendChild(userLine);
  input.value = "";

  const res = await fetch(`${BACKEND}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target_id: currentTargetId(), message }),
  });
  const data = await res.json();
  const botLine = document.createElement("div");
  botLine.className = "bot";
  botLine.textContent = `Bot (${data.mode}): ${data.reply}`;
  log.appendChild(botLine);
  log.scrollTop = log.scrollHeight;
}

async function renderLatestChart() {
  const targetId = currentTargetId();
  const rows = await fetch(`${BACKEND}/api/runs/latest?target_id=${targetId}`).then((r) => r.json());
  const ctx = document.getElementById("latest-chart");
  if (latestChart) latestChart.destroy();
  latestChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: rows.map((r) => r.metric_key),
      datasets: [{ label: "Score", data: rows.map((r) => r.score) }],
    },
    options: { scales: { y: { min: 0, max: 1 } } },
  });
}

async function renderTrendChart() {
  const targetId = currentTargetId();
  const metricKey = document.getElementById("metric-select").value;
  const rows = await fetch(
    `${BACKEND}/api/history?target_id=${targetId}&metric_key=${metricKey}`
  ).then((r) => r.json());
  const ctx = document.getElementById("trend-chart");
  if (trendChart) trendChart.destroy();
  trendChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: rows.map((r) => r.ts),
      datasets: [{ label: metricKey, data: rows.map((r) => r.score) }],
    },
    options: { scales: { y: { min: 0, max: 1 } } },
  });
}

document.getElementById("chat-send-btn").addEventListener("click", sendChatMessage);
document.getElementById("chat-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendChatMessage();
});
document.getElementById("run-btn").addEventListener("click", async () => {
  await fetch(`${BACKEND}/api/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target_id: currentTargetId(), metric_key: document.getElementById("metric-select").value }),
  });
  await renderLatestChart();
  await renderTrendChart();
});
document.getElementById("target-select").addEventListener("change", () => {
  renderLatestChart();
  renderTrendChart();
});
document.getElementById("metric-select").addEventListener("change", renderTrendChart);

(async () => {
  await loadTargets();
  await loadMetrics();
  await renderLatestChart();
  await renderTrendChart();
})();
```

- [ ] **Step 3: Manual verification checklist**

1. With backend running and extension loaded (Task 11 done), open sidebar → "Open dashboard".
2. Dashboard tab opens, target/metric dropdowns populate.
3. Type a message in chat input, press Enter — bot reply appears, tagged with mode (`mock`/`http`/`dom`).
4. Click "Run judge" — bar chart renders with the latest score per metric; trend line chart updates for the selected metric.
5. Run judge twice — trend chart shows two points.

- [ ] **Step 4: Commit**

```bash
git add ChatbotExtension/dashboard
git commit -m "feat: add dashboard tab with live chat and per-run/trend charts"
```

---

### Task 13: DOM-relay content script (optional target type wiring)

**Files:**
- Create: `ChatbotExtension/content_script.js`
- Modify: `ChatbotExtension/sidebar/sidebar.html` (add DOM option to target-type dropdown)
- Modify: `ChatbotExtension/sidebar/sidebar.js` (handle DOM target creation)

**Interfaces:**
- Consumes: `POST /api/relay` (Task 9), `chrome.scripting.executeScript` (MV3 API, permission already granted in Task 11's manifest).

- [ ] **Step 1: Write content_script.js**

```javascript
// ChatbotExtension/content_script.js
// Injected on demand into a chatbot's own tab. Watches for new text nodes
// appended to a user-designated container and relays them to the backend.
// The user picks the container via a one-time click-to-select prompt.
(function () {
  const BACKEND = "http://127.0.0.1:8000";
  const sessionId = window.location.hostname;

  function relay(text) {
    fetch(`${BACKEND}/api/relay`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, text }),
    }).catch(() => {
      // Backend not running yet — the DOM target's chat() call will time
      // out and surface as a normal target error, so nothing to do here.
    });
  }

  function startWatching(container) {
    const observer = new MutationObserver((mutations) => {
      for (const m of mutations) {
        for (const node of m.addedNodes) {
          const text = node.textContent?.trim();
          if (text) relay(text);
        }
      }
    });
    observer.observe(container, { childList: true, subtree: true });
  }

  function armSelection() {
    document.body.style.cursor = "crosshair";
    document.addEventListener(
      "click",
      (e) => {
        e.preventDefault();
        document.body.style.cursor = "";
        startWatching(e.target);
        relay(`[relay armed on <${e.target.tagName.toLowerCase()}>]`);
      },
      { once: true, capture: true }
    );
  }

  armSelection();
})();
```

- [ ] **Step 2: Add DOM option to sidebar.html**

Add inside the `#target-type` `<select>` in `sidebar/sidebar.html`:

```html
<option value="dom">DOM (scrape open tab)</option>
```

- [ ] **Step 3: Extend sidebar.js's save handler**

Replace the `save-target-btn` click handler in `sidebar/sidebar.js` with:

```javascript
document.getElementById("save-target-btn").addEventListener("click", async () => {
  const name = document.getElementById("target-name").value.trim();
  const type = document.getElementById("target-type").value;
  const baseUrl = document.getElementById("target-base-url").value.trim();

  let config = {};
  if (type === "http") {
    config = { base_url: baseUrl };
  } else if (type === "dom") {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    config = { session_id: new URL(tab.url).hostname };
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["content_script.js"],
    });
  }

  await fetch(`${BACKEND}/api/targets`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: name || type, type, config }),
  });
  document.getElementById("add-target-form").style.display = "none";
  await loadTargets();
});
```

- [ ] **Step 4: Manual verification checklist**

1. Open a page with any chat widget in a new tab.
2. In the extension sidebar, add a target of type "DOM (scrape open tab)" while that tab is active — cursor becomes a crosshair on the target page.
3. Click the chatbot's reply area once — content script starts watching.
4. Trigger a reply on the page; confirm (via a temporary log in `/api/relay`, or checking `GET /api/history` after a run) that text arrives.
5. Run judge against the DOM target from the dashboard — confirm it either scores the relayed text or times out cleanly with a visible error (per spec's error handling section).

- [ ] **Step 5: Commit**

```bash
git add ChatbotExtension/content_script.js ChatbotExtension/sidebar
git commit -m "feat: add DOM-relay content script for API-less chatbots"
```

---

### Task 14: Migrate/retire old evals scaffold

**Files:**
- Delete: `evals/conftest.py`, `evals/test_answer_quality.py`, `evals/test_faithfulness.py`, `evals/test_safety.py`, `evals/datasets/chatbot_golden.json` (superseded by `backend/metrics_catalog.py`, `backend/datasets/goldens.json`, and `tests/`)
- Delete: `backend.py` (root Flask backend, superseded by `backend/dashboard/app.py`)
- Delete: `start-backend.bat` (superseded by `run-backend.bat` from Task 10)
- Modify: `README.md`

**Interfaces:** none — cleanup only, verified by re-running the full test suite.

- [ ] **Step 1: Confirm no remaining references to deleted files**

Run: `grep -rln "evals/" --include=*.py --include=*.md .`
Run: `grep -rln "backend\.py\b" --include=*.bat --include=*.md .`
Expected: only this plan and the design spec reference the old paths (in prose, not as imports); no `.py` file imports from `evals`.

- [ ] **Step 2: Delete superseded files**

```bash
git rm -r evals backend.py start-backend.bat
```

- [ ] **Step 3: Update README.md**

```markdown
# Chrome Sidebar LLM Judge

Judge any chatbot's answers with DeepEval metrics, from a Chrome sidebar.

## Structure

See `docs/superpowers/specs/2026-09-16-chrome-sidebar-llm-judge-design.md`
for the full design, and `docs/superpowers/plans/2026-09-16-chrome-sidebar-llm-judge.md`
for how it was built.

- `backend/` — FastAPI app, target connectors, judge model, golden dataset, metrics catalog
- `tests/` — pytest suite (`-m smoke` for zero-cost wiring checks)
- `ChatbotExtension/` — MV3 Chrome extension (sidebar + dashboard tab)

## Setup

```bash
pip install -r requirements.txt
export JUDGE_API_KEY=...   # judge model API key (Groq/OpenAI-compatible)
run-backend.bat             # starts FastAPI on http://127.0.0.1:8000
```

Load `ChatbotExtension/` as an unpacked extension in `chrome://extensions`.

## Run tests

```bash
python -m pytest tests/ -m smoke -v   # fast, no judge tokens spent
python -m pytest tests/ -v            # full suite, requires JUDGE_API_KEY for live metric tests
```
```

- [ ] **Step 4: Run full test suite to confirm nothing broke**

Run: `python -m pytest tests/ -v`
Expected: PASS (all tests from Tasks 1-10; Tasks 11-13 have no automated tests, verified manually)

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "chore: retire Flask/pytest scaffold in favor of FastAPI backend"
```

---

## Self-Review Notes

**Spec coverage:** Purpose (chart+judging) → Tasks 6,7,9,12. Target connectors (HTTP/preset/mock/DOM) → Tasks 2,3,8,13. Judge separation → Task 5. Golden CRUD → Tasks 4,9. Metrics catalog → Task 6. SQLite history → Tasks 1,7. Chrome sidebar → Task 11. Dashboard chat+charts → Task 12. Error handling (unreachable target, missing judge key, empty dataset, relay timeout) → covered in Tasks 7,8,9 tests. Testing section (pytest smoke + unit tests, manual extension checklist) → Tasks 10,11,12,13. Migration section → Task 14.

**Placeholder scan:** no TBD/TODO; all code blocks are complete, runnable snippets; content_script.js's empty `.catch()` is an intentional no-op with an explanatory comment, not a placeholder.

**Type consistency:** `ChatbotClient`/`ChatReply` (Task 2) used identically in Tasks 3, 8, 9. `MetricSpec` fields (`key, threshold, cases(), build_metric, build_case`) used identically in Tasks 6, 7, 9. `run_spec()` signature `(spec, judge, target, target_id, conn)` matches its Task 7 definition and Task 9's call site. `storage.record_run`/`latest_runs`/`history` signatures match between Task 1 and Task 7/9 usage. `JUDGE_API_KEY_ENV` constant (Task 5) checked identically in Task 10's smoke test.
