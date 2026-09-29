# Background Runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Runs execute as backend jobs, so closing the side panel or dashboard no longer stops them, and a finished job is announced once.

**Architecture:** A `JobQueue` in the backend runs one job (a target plus an ordered list of metrics) at a time on a worker thread, reusing `runner.run_spec`. Jobs and each run's full result live in SQLite. The extension's `runMetrics()` keeps its signature but creates a job and polls it; pages reattach to a running job; the service worker watches jobs with `chrome.alarms` and is the only sender of notifications.

**Tech Stack:** Python 3.13, FastAPI, SQLite (stdlib `sqlite3`), threading; Chrome MV3 extension (plain scripts, no build step).

**Spec:** `docs/superpowers/specs/2026-09-29-background-runs-design.md`

## Global Constraints

- Offline suite: `.venv\Scripts\python -m pytest` must stay green; no new Python or JS dependencies.
- Jobs run one at a time, in creation order (token counts per run rely on it).
- `POST /api/run` and `GET /api/run/progress` stay and keep working.
- CORS and TrustedHost settings in `app.py` stay as they are.
- `minimum_chrome_version` stays `"116"`; the only new permission is `"alarms"`.
- Job statuses: `queued`, `running`, `done`, `cancelled`, `error`, `interrupted`.
- Interrupted message: "the backend stopped before this run finished; run it again".
- Times are ISO-8601 UTC strings like `2026-09-29T10:00:00Z` (`runner._now_iso()`).

## Review Focus

- Backend restarted mid-job → the job reads `interrupted`; a page following it stops polling and shows the message (Task 1 test; Task 6 Node check).
- Two jobs started quickly (side panel and dashboard) → the second stays `queued` and runs after the first (Task 3 test).
- Chatbot deleted while its job is queued → the job ends `error` with "chatbot deleted" (Task 4 test).
- Backend unreachable while a page polls → it keeps retrying, reports offline, and does not repeat results (Task 6 Node check).
- Cancel a queued job → it never starts and ends `cancelled` (Task 3 test).

---

### Task 1: Jobs table and run results in storage

**Files:** Modify `backend/storage.py`; Test `tests/test_storage.py`

**Interfaces — produces:**
- `storage.add_job(conn, target_id: int, metric_keys: list[str], options: dict) -> int`
- `storage.get_job(conn, job_id: int) -> dict | None` (decoded: `metric_keys` list, `options` dict, `results` list, `current` dict | None)
- `storage.update_job(conn, job_id: int, **fields) -> None` (dict/list values JSON-encoded; keys are code-defined column names only)
- `storage.active_jobs(conn, target_id: int) -> list[dict]` (`queued`/`running`, newest first)
- `storage.next_queued_job(conn) -> dict | None` (oldest `queued`)
- `storage.record_run(..., result_json: str | None = None)`; column `runs.result_json`
- `storage.JOB_INTERRUPTED = "the backend stopped before this run finished; run it again"`

- [ ] **Step 1: Failing tests** (append to `tests/test_storage.py`)

```python
def test_jobs_are_stored_listed_and_updated(tmp_path):
    conn = storage.init_db(str(tmp_path / "j.db"))
    first = storage.add_job(conn, 1, ["bias", "toxicity"], {"limit": 3})
    second = storage.add_job(conn, 1, ["bias"], {})
    job = storage.get_job(conn, first)
    assert job["status"] == "queued" and job["metric_keys"] == ["bias", "toxicity"]
    assert job["options"] == {"limit": 3} and job["results"] == [] and job["current"] is None
    assert storage.next_queued_job(conn)["id"] == first
    storage.update_job(conn, first, status="running", current={"metric_key": "bias"})
    assert storage.get_job(conn, first)["current"] == {"metric_key": "bias"}
    assert [j["id"] for j in storage.active_jobs(conn, 1)] == [second, first]
    storage.update_job(conn, first, status="done", results=[{"key": "bias"}])
    assert storage.next_queued_job(conn)["id"] == second
    assert [j["id"] for j in storage.active_jobs(conn, 1)] == [second]
    assert storage.get_job(conn, 999) is None


def test_unfinished_jobs_are_interrupted_on_start(tmp_path):
    path = str(tmp_path / "j.db")
    conn = storage.init_db(path)
    running = storage.add_job(conn, 1, ["bias"], {})
    storage.update_job(conn, running, status="running")
    queued = storage.add_job(conn, 1, ["bias"], {})
    done = storage.add_job(conn, 1, ["bias"], {})
    storage.update_job(conn, done, status="done")
    conn.close()
    conn = storage.init_db(path)
    for job_id in (running, queued):
        job = storage.get_job(conn, job_id)
        assert job["status"] == "interrupted" and job["error"] == storage.JOB_INTERRUPTED
    assert storage.get_job(conn, done)["status"] == "done"


def test_runs_keep_their_full_result(tmp_path):
    conn = storage.init_db(str(tmp_path / "r.db"))
    target_id = storage.add_target(conn, "bot", "http", {})
    storage.record_run(conn, target_id, "m", 0.8, True, "2026-01-01T00:00:00Z", result_json='{"rows": []}')
    assert storage.latest_runs(conn, target_id)[0]["result_json"] == '{"rows": []}'
```

- [ ] **Step 2:** `.venv\Scripts\python -m pytest tests/test_storage.py -k "jobs or full_result" -v` → FAIL (no `add_job`)

- [ ] **Step 3: Implement** — add `("result_json", "TEXT")` last in `RUN_EXTRA_COLUMNS`; add `result_json` keyword + column + value to `record_run`'s INSERT. In `init_db` after the documents table:

```python
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                target_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                metric_keys TEXT NOT NULL,
                options TEXT NOT NULL DEFAULT '{}',
                results TEXT NOT NULL DEFAULT '[]',
                current TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            )
            """
        )
        # A job lives in the backend process: one still queued or running when
        # the backend stopped will never finish.
        conn.execute(
            "UPDATE jobs SET status = 'interrupted', error = ?, current = NULL "
            "WHERE status IN ('queued', 'running')",
            (JOB_INTERRUPTED,),
        )
```

Module level:

```python
JOB_INTERRUPTED = "the backend stopped before this run finished; run it again"
_JOB_JSON = {"metric_keys": [], "options": {}, "results": [], "current": None}


def _job(row) -> dict:
    job = dict(row)
    for key, empty in _JOB_JSON.items():
        job[key] = json.loads(job[key]) if job[key] else empty
    return job


def add_job(conn: sqlite3.Connection, target_id: int, metric_keys: list[str], options: dict) -> int:
    with _LOCK:
        cur = conn.execute(
            "INSERT INTO jobs (target_id, status, metric_keys, options, created_at) VALUES (?, 'queued', ?, ?, ?)",
            (target_id, json.dumps(metric_keys), json.dumps(options), _now_iso()),
        )
        conn.commit()
        return cur.lastrowid


def get_job(conn: sqlite3.Connection, job_id: int) -> dict | None:
    with _LOCK:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return _job(row) if row else None


def update_job(conn: sqlite3.Connection, job_id: int, **fields) -> None:
    encoded = {k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in fields.items()}
    assignments = ", ".join(f"{k} = ?" for k in encoded)
    with _LOCK:
        conn.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", (*encoded.values(), job_id))
        conn.commit()


def active_jobs(conn: sqlite3.Connection, target_id: int) -> list[dict]:
    with _LOCK:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE target_id = ? AND status IN ('queued', 'running') ORDER BY id DESC",
            (target_id,),
        ).fetchall()
    return [_job(r) for r in rows]


def next_queued_job(conn: sqlite3.Connection) -> dict | None:
    with _LOCK:
        row = conn.execute("SELECT * FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone()
    return _job(row) if row else None
```

- [ ] **Step 4:** `.venv\Scripts\python -m pytest tests/test_storage.py -v` → PASS
- [ ] **Step 5: Commit** `feat: jobs table and full run results in storage`

---

### Task 2: Runner saves each run's full result

**Files:** Modify `backend/dashboard/runner.py` (the `storage.record_run(...)` call and the `return {...}` after it); Test `tests/test_runner.py`

**Interfaces:** consumes `record_run(result_json=...)`; produces `runs.result_json` = `json.dumps` of the dict `run_spec` returns.

- [ ] **Step 1: Failing test**

```python
def test_recorded_run_keeps_the_full_result(tmp_path):
    import json as _json
    conn, target_id = _db(tmp_path)
    spec = _fake_spec([_case("What is your refund window?")], _fake_metric([0.9], [True]))
    result = run_spec(spec, judge=object(), target=CannedChatbot(), target_id=target_id, conn=conn)
    saved = _json.loads(storage.latest_runs(conn, target_id)[0]["result_json"])
    assert saved == _json.loads(_json.dumps(result))
    assert saved["rows"][0]["question"] == "What is your refund window?"
```

- [ ] **Step 2:** run it → FAIL (`result_json` is None)
- [ ] **Step 3: Implement** — `import json`; turn the returned literal into `result = {...}` (verbatim), then:

```python
    # One row per run: the charts plot run averages, not individual cases; the
    # full result lets Details and the report show the cases from any browser.
    storage.record_run(
        conn, target_id, spec.key, avg, passed, _now_iso(), cases_run=len(rows),
        judge_model=judge_model, judge_tokens=judge_tokens, judge_calls=judge_calls,
        target_calls=chatbot_calls, duration_s=duration, judge_spread=spread,
        cases_skipped=skipped, result_json=json.dumps(result, default=str),
    )
    return result
```

(`previous_model` is still read before `record_run`.)

- [ ] **Step 4:** `.venv\Scripts\python -m pytest tests/test_runner.py -v` → PASS
- [ ] **Step 5: Commit** `feat: save each run's full result`

---

### Task 3: Job queue

**Files:** Create `backend/dashboard/jobs.py`; Test `tests/test_jobs.py`

**Interfaces — produces:**
- `JobQueue(conn, run_metric)`; `run_metric(job: dict, metric_key: str, on_progress) -> dict` returns a `run_spec`-style result or raises `JobFailed(message)` to end the whole job
- `submit(target_id, metric_keys, options) -> int`, `get(job_id) -> dict | None` (with live `current`), `active(target_id) -> list[dict]`, `cancel(job_id) -> bool`, `process_next() -> bool`, `start() -> None`
- `class JobFailed(Exception)`

- [ ] **Step 1: Failing tests** — `tests/test_jobs.py`

```python
import threading

from backend import storage
from backend.dashboard.jobs import JobFailed, JobQueue
from backend.dashboard.runner import RunCancelled


def _queue(tmp_path, run_metric):
    conn = storage.init_db(str(tmp_path / "q.db"))
    return JobQueue(conn, run_metric), conn


def test_jobs_run_in_order_one_at_a_time(tmp_path):
    seen = []
    queue, _ = _queue(tmp_path, lambda job, key, p: seen.append((job["id"], key)) or {"key": key, "status": "pass"})
    first = queue.submit(1, ["a", "b"], {})
    second = queue.submit(1, ["c"], {})
    assert queue.get(second)["status"] == "queued"
    assert queue.process_next() and queue.get(second)["status"] == "queued"
    assert queue.process_next() and not queue.process_next()
    assert seen == [(first, "a"), (first, "b"), (second, "c")]
    job = queue.get(first)
    assert job["status"] == "done" and [r["key"] for r in job["results"]] == ["a", "b"]
    assert job["current"] is None and job["finished_at"]


def test_a_failing_metric_does_not_stop_the_job(tmp_path):
    def run(job, key, p):
        return {"key": key, "status": "error", "error": "HTTP 500"} if key == "a" else {"key": key, "status": "pass"}
    queue, _ = _queue(tmp_path, run)
    job_id = queue.submit(1, ["a", "b"], {})
    queue.process_next()
    assert [r["status"] for r in queue.get(job_id)["results"]] == ["error", "pass"]
    assert queue.get(job_id)["status"] == "done"


def test_job_failed_ends_the_job_with_its_message(tmp_path):
    def run(job, key, p):
        raise JobFailed("chatbot deleted")
    queue, _ = _queue(tmp_path, run)
    job_id = queue.submit(1, ["a", "b"], {})
    queue.process_next()
    job = queue.get(job_id)
    assert job["status"] == "error" and job["error"] == "chatbot deleted" and job["results"] == []


def test_cancel_while_running_stops_at_the_next_step(tmp_path):
    holder = {}

    def run(job, key, on_progress):
        holder["queue"].cancel(job["id"])
        try:
            on_progress(0, 1, "chat", "q")
        except RunCancelled:
            return {"key": key, "status": "cancelled"}
        return {"key": key, "status": "pass"}

    queue, _ = _queue(tmp_path, run)
    holder["queue"] = queue
    job_id = queue.submit(1, ["a", "b"], {})
    queue.process_next()
    job = queue.get(job_id)
    assert job["status"] == "cancelled" and [r["key"] for r in job["results"]] == ["a"]


def test_cancel_while_queued_never_starts(tmp_path):
    calls = []
    queue, _ = _queue(tmp_path, lambda job, key, p: calls.append(key) or {"key": key, "status": "pass"})
    job_id = queue.submit(1, ["a"], {})
    assert queue.cancel(job_id)
    assert not queue.process_next()
    assert calls == [] and queue.get(job_id)["status"] == "cancelled"
    assert not queue.cancel(job_id)


def test_live_progress_is_visible_while_running(tmp_path):
    seen, holder = {}, {}

    def run(job, key, on_progress):
        on_progress(1, 4, "judge", "Where is my order?")
        seen.update(holder["queue"].get(job["id"])["current"])
        return {"key": key, "status": "pass"}

    queue, _ = _queue(tmp_path, run)
    holder["queue"] = queue
    queue.submit(1, ["a"], {})
    queue.process_next()
    assert seen["metric_key"] == "a" and seen["index"] == 0 and seen["count"] == 1
    assert (seen["done"], seen["total"], seen["phase"], seen["question"]) == (1, 4, "judge", "Where is my order?")
    assert seen["started_at"].endswith("Z")


def test_worker_thread_runs_submitted_jobs(tmp_path):
    finished = threading.Event()

    def run(job, key, p):
        finished.set()
        return {"key": key, "status": "pass"}

    queue, _ = _queue(tmp_path, run)
    queue.start()
    queue.submit(1, ["a"], {})
    assert finished.wait(5)
```

- [ ] **Step 2:** `.venv\Scripts\python -m pytest tests/test_jobs.py -v` → FAIL (module missing)
- [ ] **Step 3: Implement** `backend/dashboard/jobs.py`

```python
"""Background runs: a job is one batch of metrics for one chatbot.

Jobs run one at a time, in creation order, on a single worker thread, so a run
keeps going after the page that started it closes, and the token counts each run
records (the change in the usage counters) stay per run. Jobs and their results
live in SQLite; live progress is kept in memory and merged in by get().
"""
from __future__ import annotations

import sqlite3
import threading

from backend import storage
from backend.dashboard.runner import RunCancelled, _now_iso


class JobFailed(Exception):
    """Raised by run_metric when the whole job cannot go on (judge missing, chatbot deleted)."""


class JobQueue:
    def __init__(self, conn: sqlite3.Connection, run_metric):
        self._conn = conn
        self._run_metric = run_metric
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._cancelled: set[int] = set()
        self._current: dict[int, dict] = {}
        self._thread: threading.Thread | None = None

    def submit(self, target_id: int, metric_keys: list[str], options: dict) -> int:
        job_id = storage.add_job(self._conn, target_id, list(metric_keys), dict(options))
        self._wake.set()
        return job_id

    def get(self, job_id: int) -> dict | None:
        job = storage.get_job(self._conn, job_id)
        if job is not None:
            with self._lock:
                live = self._current.get(job_id)
            if live is not None:
                job["current"] = dict(live)
        return job

    def active(self, target_id: int) -> list[dict]:
        return [self.get(j["id"]) for j in storage.active_jobs(self._conn, target_id)]

    def cancel(self, job_id: int) -> bool:
        job = storage.get_job(self._conn, job_id)
        if job is None or job["status"] not in ("queued", "running"):
            return False
        with self._lock:
            self._cancelled.add(job_id)
        if job["status"] == "queued":
            storage.update_job(self._conn, job_id, status="cancelled", finished_at=_now_iso())
        return True

    def process_next(self) -> bool:
        job = storage.next_queued_job(self._conn)
        if job is None:
            return False
        job_id, keys = job["id"], job["metric_keys"]
        storage.update_job(self._conn, job_id, status="running", started_at=_now_iso())
        results: list[dict] = []
        status, error = "done", None
        for index, key in enumerate(keys):
            if self._is_cancelled(job_id):
                status = "cancelled"
                break
            base = {"metric_key": key, "index": index, "count": len(keys), "started_at": _now_iso()}
            self._set_current(job_id, {**base, "done": 0, "total": 0, "phase": "chat", "question": ""})

            def on_progress(done, total, phase, question, _base=base):
                self._set_current(job_id, {**_base, "done": done, "total": total, "phase": phase, "question": question})
                if self._is_cancelled(job_id):
                    raise RunCancelled()

            try:
                result = self._run_metric(job, key, on_progress)
            except JobFailed as e:
                status, error = "error", str(e)
                break
            results.append(result)
            storage.update_job(self._conn, job_id, results=results)
            if result.get("status") == "cancelled":
                status = "cancelled"
                break
        with self._lock:
            self._current.pop(job_id, None)
            self._cancelled.discard(job_id)
        storage.update_job(self._conn, job_id, status=status, error=error, results=results,
                           current=None, finished_at=_now_iso())
        return True

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="llm-judge-jobs", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while True:
            try:
                ran = self.process_next()
            except Exception:  # noqa: BLE001 - the worker must survive one bad job
                ran = False
            if not ran:
                self._wake.wait(timeout=5)
                self._wake.clear()

    def _set_current(self, job_id: int, current: dict) -> None:
        with self._lock:
            self._current[job_id] = current

    def _is_cancelled(self, job_id: int) -> bool:
        with self._lock:
            return job_id in self._cancelled
```

- [ ] **Step 4:** `.venv\Scripts\python -m pytest tests/test_jobs.py -v` → PASS
- [ ] **Step 5: Commit** `feat: background job queue for runs`

---

### Task 4: Job API

**Files:** Modify `backend/dashboard/app.py`, `CLAUDE.md`; Test `tests/test_app.py`

**Interfaces:**
- Consumes: `JobQueue`, `JobFailed`; `run_spec`; `_target_or_404`, `_client_or_400`, `build_judge`, `_judge_overrides`, `SPECS_BY_KEY`, `DEFAULT_THEME`; the existing single-target getter in `backend/storage.py` (check its name)
- Produces (HTTP):
  - `POST /api/jobs` `{target_id, metric_keys, thresholds = {}, limit = null, check_consistency = false}` → `{job_id, status}`; 404 unknown target/metric; 400 empty list; 503 judge not configured
  - `GET /api/jobs/{job_id}` → job or 404; `GET /api/jobs?target_id=&active=1` → active jobs
  - `POST /api/jobs/{job_id}/cancel` (body `{}`) → `{cancelling: bool}`
  - `GET /api/runs/latest` rows gain `result` (dict | null) and lose `result_json`

- [ ] **Step 1: Failing tests** (append to `tests/test_app.py`)

```python
def _run_jobs(app_module):
    """Runs every queued job now (tests do not rely on the worker thread's timing)."""
    while app_module.jobs.process_next():
        pass


def test_job_runs_every_metric_and_records_them(client, app_module):
    target = _create_target(client)
    with patch.object(app_module, "build_judge", return_value=object()), \
         patch.object(app_module, "SPECS_BY_KEY", {"answer_relevancy": _fake_spec([]), "bias": _fake_spec([])}), \
         patch.object(app_module.HttpTargetClient, "chat", return_value=_REPLY):
        created = client.post("/api/jobs", json={"target_id": target["id"],
                                                 "metric_keys": ["answer_relevancy", "bias"],
                                                 "thresholds": {"bias": 0.5}}).json()
        assert created["status"] == "queued"
        active = client.get("/api/jobs", params={"target_id": target["id"], "active": 1}).json()
        assert [j["id"] for j in active] == [created["job_id"]]
        _run_jobs(app_module)
    job = client.get(f"/api/jobs/{created['job_id']}").json()
    assert job["status"] == "done" and len(job["results"]) == 2
    assert client.get("/api/jobs", params={"target_id": target["id"], "active": 1}).json() == []
    latest = client.get("/api/runs/latest", params={"target_id": target["id"]}).json()
    assert latest and latest[0]["result"]["rows"] and "result_json" not in latest[0]


def test_job_requests_are_validated(client, app_module):
    target = _create_target(client)
    assert client.post("/api/jobs", json={"target_id": 999999, "metric_keys": ["bias"]}).status_code == 404
    assert client.post("/api/jobs", json={"target_id": target["id"], "metric_keys": ["nope"]}).status_code == 404
    assert client.post("/api/jobs", json={"target_id": target["id"], "metric_keys": []}).status_code == 400
    assert client.get("/api/jobs/999999").status_code == 404
    with patch.object(app_module, "build_judge", side_effect=RuntimeError("no judge key")):
        r = client.post("/api/jobs", json={"target_id": target["id"], "metric_keys": ["bias"]})
    assert r.status_code == 503


def test_job_for_a_deleted_chatbot_fails_clearly(client, app_module):
    target = _create_target(client)
    with patch.object(app_module, "build_judge", return_value=object()):
        job_id = client.post("/api/jobs", json={"target_id": target["id"], "metric_keys": ["bias"]}).json()["job_id"]
        client.delete(f"/api/targets/{target['id']}")
        _run_jobs(app_module)
    job = client.get(f"/api/jobs/{job_id}").json()
    assert job["status"] == "error" and job["error"] == "chatbot deleted"


def test_queued_job_can_be_cancelled(client, app_module):
    target = _create_target(client)
    with patch.object(app_module, "build_judge", return_value=object()):
        job_id = client.post("/api/jobs", json={"target_id": target["id"], "metric_keys": ["bias"]}).json()["job_id"]
    assert client.post(f"/api/jobs/{job_id}/cancel", json={}).json() == {"cancelling": True}
    _run_jobs(app_module)
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "cancelled"
```

- [ ] **Step 2:** `.venv\Scripts\python -m pytest tests/test_app.py -k "job" -v` → FAIL (404)
- [ ] **Step 3: Implement** — `from backend.dashboard.jobs import JobFailed, JobQueue`; after `_conn = storage.init_db(DB_PATH)` and the progress globals:

```python
def _run_job_metric(job: dict, metric_key: str, on_progress) -> dict:
    """Runs one metric of a background job, exactly as /api/run does."""
    row = storage.get_target(_conn, job["target_id"])
    if row is None:
        raise JobFailed("chatbot deleted")
    spec = SPECS_BY_KEY.get(metric_key)
    if spec is None:
        return {"key": metric_key, "status": "error", "error": "metric not found", "score": None, "rows": []}
    try:
        judge = build_judge(**_judge_overrides())
    except RuntimeError as e:
        raise JobFailed(str(e)) from e
    try:
        target = _client_or_400(row)
    except HTTPException as e:
        raise JobFailed(str(e.detail)) from e
    options, config = job["options"], row["config"]
    return run_spec(
        spec, judge, target, job["target_id"], _conn,
        theme=config.get("theme") or DEFAULT_THEME,
        threshold=(options.get("thresholds") or {}).get(metric_key),
        on_progress=on_progress, persona=config.get("persona", ""), limit=options.get("limit"),
        probe_set=config.get("probe_set", ""), check_consistency=bool(options.get("check_consistency")),
    )


jobs = JobQueue(_conn, _run_job_metric)
# Tests run queued jobs themselves (JUDGE_START_JOB_WORKER=0) instead of racing a thread.
if os.getenv("JUDGE_START_JOB_WORKER", "1") != "0":
    jobs.start()
```

Endpoints (after `/api/run/progress`):

```python
class JobCreate(BaseModel):
    target_id: int
    metric_keys: list[str]
    thresholds: dict[str, float] = Field(default_factory=dict)
    limit: int | None = Field(default=None, ge=1)
    check_consistency: bool = False


class JobCancel(BaseModel):
    """Empty on purpose: a JSON body forces the CORS preflight, like every other POST."""


@app.post("/api/jobs")
def api_create_job(body: JobCreate):
    _target_or_404(body.target_id)
    if not body.metric_keys:
        raise HTTPException(status_code=400, detail="pick at least one metric")
    unknown = [k for k in body.metric_keys if k not in SPECS_BY_KEY]
    if unknown:
        raise HTTPException(status_code=404, detail=f"metric not found: {', '.join(unknown)}")
    try:
        build_judge(**_judge_overrides())
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    job_id = jobs.submit(body.target_id, body.metric_keys, {
        "thresholds": body.thresholds, "limit": body.limit, "check_consistency": body.check_consistency,
    })
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: int):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@app.get("/api/jobs")
def api_list_jobs(target_id: int, active: int = 1):
    return jobs.active(target_id)


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel_job(job_id: int, _body: JobCancel):
    return {"cancelling": jobs.cancel(job_id)}
```

In `/api/runs/latest`: for each row, `raw = row.pop("result_json", None)`; `row["result"] = json.loads(raw) if raw else None`. In `/api/history`, pop `result_json` from each row (the trend does not need it).

In the `app_module` test fixture add `monkeypatch.setenv("JUDGE_START_JOB_WORKER", "0")`. (The app module is imported once per test session, so set the variable before its first import — in the fixture, as the other env vars are.)

`CLAUDE.md` runner bullet, append: "Runs from the extension go through `backend/dashboard/jobs.py`: `POST /api/jobs` queues a batch (one job at a time on a worker thread, reusing `run_spec`), `GET /api/jobs/{id}` reports progress and results, unfinished jobs become `interrupted` on start, and each run's full result is stored in `runs.result_json` (returned as `result` by `/api/runs/latest`)."

- [ ] **Step 4:** `.venv\Scripts\python -m pytest -q` → PASS
- [ ] **Step 5: Commit** `feat: job API for background runs`

---

### Task 5: Service worker announces jobs

**Files:** Create `ChatbotExtension/lib/announce.js`; Modify `lib/ui.js`, `background.js`, `manifest.json`, `sidebar/sidebar.html`, `dashboard/dashboard.html`, `report/report.html`, `sidebar/sidebar.js`, `dashboard/dashboard.js`

**Interfaces:**
- Consumes: `GET /api/jobs/{id}`
- Produces: `runAnnouncement(target, keys, results, metrics)` (unchanged), `jobAnnouncement(job, target, metrics) -> summary | null`; worker messages `WATCH_JOB {jobId, target: {id, name}, metrics: [{key, title}]}` and `JOB_ENDED {jobId}`

- [ ] **Step 1:** Move `runAnnouncement` from `ui.js` into new `lib/announce.js` unchanged; add:

```javascript
// What a finished job says: its results, why it failed, or that it was cut off.
function jobAnnouncement(job, target, metrics) {
  if (job.status === "interrupted") {
    return { outcome: "error", badge: "!", title: "LLM Judge · run interrupted",
             message: `${target.name}: the backend stopped before this run finished; run it again.` };
  }
  if (job.status === "error") {
    return { outcome: "error", badge: "!", title: "LLM Judge · run failed",
             message: `${target.name}: ${job.error || "the run could not start"}` };
  }
  if (job.status !== "done") return null;  // cancelled: the user stopped it
  return runAnnouncement(target, job.metric_keys, job.results, metrics);
}
```

Delete `announceRun` from `ui.js` and its call sites (`sidebar.js`: `announceRun(target, runnable, results, state.metrics);`; `dashboard.js`: `announceRun(target, keys, results, state.metrics);`). Load `<script src="../lib/announce.js"></script>` before `ui.js` in the three HTML pages.

- [ ] **Step 2:** `background.js` — first lines: `importScripts("lib/announce.js");` and `const BACKEND_URL = "http://127.0.0.1:8000";`. Add:

```javascript
// Jobs this browser started and has not announced yet: {jobId: {target, metrics}}.
async function watched() {
  return (await chrome.storage.session.get("watchedJobs")).watchedJobs || {};
}

let checking = Promise.resolve();
function checkJobs() {
  // One check at a time, so a job cannot be announced twice.
  checking = checking.then(checkJobsNow).catch((error) => console.error("LLM Judge: job check failed", error));
  return checking;
}

async function checkJobsNow() {
  const jobs = await watched();
  for (const [jobId, info] of Object.entries(jobs)) {
    let job;
    try {
      const response = await fetch(`${BACKEND_URL}/api/jobs/${jobId}`);
      if (response.status === 404) { delete jobs[jobId]; continue; }
      job = await response.json();
    } catch {
      continue;  // backend unreachable: try again at the next alarm
    }
    if (job.status === "queued" || job.status === "running") continue;
    delete jobs[jobId];
    const summary = jobAnnouncement(job, info.target, info.metrics);
    if (summary) await runFinished(summary);
  }
  await chrome.storage.session.set({ watchedJobs: jobs });
  if (!Object.keys(jobs).length) await chrome.alarms.clear("watch-jobs");
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "watch-jobs") checkJobs();
});
```

Replace the `RUN_FINISHED` message branch with:

```javascript
  } else if (message.type === "WATCH_JOB" && message.jobId) {
    watched()
      .then((jobs) => chrome.storage.session.set({
        watchedJobs: { ...jobs, [message.jobId]: { target: message.target, metrics: message.metrics || [] } },
      }))
      .then(() => chrome.alarms.create("watch-jobs", { periodInMinutes: 0.5 }))
      .catch((error) => console.error("LLM Judge: could not watch the run", error));
  } else if (message.type === "JOB_ENDED") {
    checkJobs();
```

Add `"alarms"` to `manifest.json` permissions.

- [ ] **Step 3:** `node --check` each changed JS file → no output
- [ ] **Step 4: Node check**

```bash
node -e '
const vm = require("vm"), fs = require("fs"), assert = require("assert");
vm.runInThisContext(fs.readFileSync("ChatbotExtension/lib/announce.js", "utf8"));
const t = { name: "Shop bot" }, m = [{ key: "bias", title: "Bias" }];
assert.strictEqual(jobAnnouncement({ status: "cancelled" }, t, m), null);
assert.strictEqual(jobAnnouncement({ status: "interrupted" }, t, m).title, "LLM Judge · run interrupted");
assert.ok(jobAnnouncement({ status: "error", error: "chatbot deleted" }, t, m).message.endsWith("chatbot deleted"));
const done = jobAnnouncement({ status: "done", metric_keys: ["bias"], results: [{ key: "bias", status: "fail" }] }, t, m);
assert.strictEqual(done.badge, "1");
console.log("announce ok");'
```

Expected: `announce ok`

- [ ] **Step 5: Commit** `feat: service worker announces finished jobs`

---

### Task 6: Extension runs through jobs and reattaches

**Files:** Modify `ChatbotExtension/lib/ui.js`, `sidebar/sidebar.js`, `dashboard/dashboard.js`, `report/report.js`, `README.md`

**Interfaces:**
- Consumes: job API; `WATCH_JOB`/`JOB_ENDED`
- Produces: `runMetrics(target, metricKeys, onProgress, control) -> Promise<results[]>` (same signature and callbacks as today; `control.metrics` = catalog for titles), `followJob(target, jobId, onProgress, control) -> Promise<results[]>` (sets `control.jobStatus`, `control.jobError`; calls `control.onOffline(bool)`), `activeJob(target) -> Promise<job | null>`, `caseDetailsFromRun(run) -> details | null`

- [ ] **Step 1: `lib/ui.js`** — replace `runMetrics` and `stopRun`:

```javascript
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// A backend job's live progress in the shape progressText/progressFraction read.
function jobProgress(current) {
  return {
    metricIndex: current.index, metricCount: current.count, startedAt: Date.parse(current.started_at),
    done: current.done, total: current.total, phase: current.phase, question: current.question,
  };
}

// Runs the metrics as a backend job: the run goes on if this page closes, and
// the service worker announces it when it ends.
async function runMetrics(target, metricKeys, onProgress, control = {}) {
  const thresholds = await settings.get("thresholds", {});
  const checkConsistency = await settings.get("checkConsistency", false);
  const picked = Object.fromEntries(metricKeys
    .filter((key) => typeof thresholds[key] === "number")
    .map((key) => [key, thresholds[key]]));
  const job = await api("/api/jobs", {
    method: "POST",
    body: {
      target_id: target.id, metric_keys: metricKeys, thresholds: picked,
      limit: control.limit || undefined, check_consistency: checkConsistency,
    },
  });
  watchJob(target, job.job_id, control.metrics || []);
  return followJob(target, job.job_id, onProgress, control);
}

function watchJob(target, jobId, metrics) {
  chrome.runtime.sendMessage({
    type: "WATCH_JOB", jobId, target: { id: target.id, name: target.name },
    metrics: metrics.map((m) => ({ key: m.key, title: m.title })),
  }).catch(() => {});
}

// Polls a job until it ends, reporting each metric once. A backend that stops
// answering is retried (control.onOffline tells the page); results are not repeated.
async function followJob(target, jobId, onProgress, control = {}) {
  control.jobId = jobId;
  const reported = new Set();
  for (;;) {
    let job;
    try {
      job = await api(`/api/jobs/${jobId}`);
      if (control.onOffline) control.onOffline(false);
    } catch (error) {
      if (control.onOffline) control.onOffline(true, error);
      await sleep(2000);
      continue;
    }
    for (const result of job.results) {
      if (reported.has(result.key)) continue;
      reported.add(result.key);
      onProgress(result.key, result);
      if (result.status !== "cancelled") {
        await settings.set("lastRun", { targetId: target.id, metricKey: result.key, finishedAt: Date.now(), result });
        await saveCaseDetails(target.id, result.key, result);
      }
    }
    if (job.current && !reported.has(job.current.metric_key)) {
      onProgress(job.current.metric_key, null, jobProgress(job.current));
    }
    if (job.status !== "queued" && job.status !== "running") {
      chrome.runtime.sendMessage({ type: "JOB_ENDED", jobId }).catch(() => {});
      control.jobStatus = job.status;
      control.jobError = job.error || null;
      return job.results;
    }
    await sleep(1000);
  }
}

async function activeJob(target) {
  try {
    const [job] = await api(`/api/jobs?target_id=${target.id}&active=1`);
    return job || null;
  } catch {
    return null;
  }
}

async function stopRun(control) {
  control.stopped = true;
  if (control.jobId) {
    await api(`/api/jobs/${control.jobId}/cancel`, { method: "POST", body: {} });
  }
}

// Case details for Details and the report from a /api/runs/latest row's stored result.
function caseDetailsFromRun(run) {
  if (!run || !run.result) return null;
  return { ...caseDetailsOf(run.result), finishedAt: Date.parse(run.ts) };
}
```

- [ ] **Step 2: Side panel** — move the Run handler's body after the skipped rows into `async function watchRun(target, keys, start)`, where `start(onProgress, control)` returns the results promise. It sets `state.runControl = { metrics: state.metrics, onOffline: (off) => { $("status").textContent = off ? "Backend not reachable, retrying…" : ""; } }`, disables Run, shows Stop, renders rows with `renderRunRow`, and in `finally` restores the buttons; after the run, if `control.jobStatus` is `error` or `interrupted`, it shows `control.jobError` in `$("status")`. The Run button calls `watchRun(target, runnable, (p, c) => runMetrics(target, runnable, p, c))`. In `onTargetChanged`, after the existing loads:

```javascript
  const job = target ? await activeJob(target) : null;
  if (job && state.runControl === null) {
    $("run-results").replaceChildren();
    watchRun(target, job.metric_keys, (p, c) => followJob(target, job.id, p, c));
  }
```

- [ ] **Step 3: Dashboard** — `runKeys(keys)` becomes `followRun(target, keys, (p, c) => runMetrics(target, keys, p, c))`; `followRun(target, keys, start)` holds today's body from `beginRun()` through `finally`, with `state.runControl.metrics = state.metrics` and `state.runControl.onOffline = (off) => off && setStatus("Backend not reachable, retrying…", true)`, and calls `start(onProgress, state.runControl)` instead of `runMetrics(...)`. After `renderLatest()` on load and on target change:

```javascript
  const job = await activeJob(target);
  if (job && !state.running) followRun(target, job.metric_keys, (p, c) => followJob(target, job.id, p, c));
```

In `toggleCardDetails`:

```javascript
    const run = latestRowsCache.find((r) => r.metric_key === key);
    const [stored] = await Promise.all([loadCaseDetails(target.id, key), loadDetailHistory(key)]);
    state.caseDetailsCache[key] = caseDetailsFromRun(run) || stored;
```

- [ ] **Step 4: Report** — in `build()`, each metric's details: `caseDetailsFromRun(latestByKey[m.key]) || await loadCaseDetails(target.id, m.key)`.

- [ ] **Step 5: Node check of `followJob`**

```bash
node -e '
global.chrome = { runtime: { sendMessage: () => Promise.resolve() }, storage: { local: { get: async () => ({}), set: async () => {} } } };
const vm = require("vm"), fs = require("fs"), assert = require("assert");
vm.runInThisContext(fs.readFileSync("ChatbotExtension/lib/ui.js", "utf8"));
global.setTimeout = (fn) => fn();
const cur = (key, index) => ({ metric_key: key, index, count: 2, done: 0, total: 3, phase: "chat", question: "q", started_at: "2026-01-01T00:00:00Z" });
let replies = [
  new Error("offline"),
  { status: "running", results: [], current: cur("a", 0) },
  { status: "running", results: [{ key: "a", status: "pass" }], current: cur("b", 1) },
  { status: "done", results: [{ key: "a", status: "pass" }, { key: "b", status: "fail" }], current: null },
];
global.api = async () => { const r = replies.shift(); if (r instanceof Error) throw r; return r; };
const events = [], offline = [];
(async () => {
  const results = await followJob({ id: 1, name: "bot" }, 7, (key, result) => events.push([key, result ? result.status : "progress"]),
                                  { onOffline: (off) => offline.push(off) });
  assert.deepStrictEqual(events, [["a", "progress"], ["a", "pass"], ["b", "progress"], ["b", "fail"]]);
  assert.deepStrictEqual(offline.slice(0, 2), [true, false]);
  assert.strictEqual(results.length, 2);
  replies = [{ status: "interrupted", error: "the backend stopped before this run finished; run it again", results: [], current: null }];
  const control = {};
  await followJob({ id: 1, name: "bot" }, 8, () => {}, control);
  assert.strictEqual(control.jobStatus, "interrupted");
  assert.ok(control.jobError.startsWith("the backend stopped"));
  console.log("followJob ok");
})();'
```

Expected: `followJob ok`

- [ ] **Step 6:** `node --check` each changed JS file; `.venv\Scripts\python -m pytest -q` → PASS
- [ ] **Step 7: README** — in "When a run finishes", replace "Runs happen in the side panel or dashboard page, so closing that page stops the run." with "Runs are backend jobs: closing the side panel or dashboard does not stop them, reopening either shows the live progress, and the notification arrives when the job ends (within 30 seconds if no page is open)."
- [ ] **Step 8: Commit** `feat: extension runs metrics as background jobs`
