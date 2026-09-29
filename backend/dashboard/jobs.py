"""Background runs: a job is one batch of metrics for one chatbot.

Jobs run one at a time, in creation order, on a single worker thread, so a run
keeps going after the page that started it closes. Each run's token counts are
the change in the process-wide usage counters, so judge or chatbot calls made
meanwhile (ad-hoc judging, golden generation, the dashboard chat) are counted
in the run that was going at the time. Jobs and their results
live in SQLite; live progress is kept in memory and merged in by get().
"""
from __future__ import annotations

import logging
import sqlite3
import threading

from backend import storage
from backend.dashboard.runner import RunCancelled, _now_iso

logger = logging.getLogger(__name__)


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
            except Exception as e:  # noqa: BLE001 - never leave a job "running" forever
                logger.exception("LLM Judge: job %s failed on %s", job_id, key)
                status, error = "error", f"{type(e).__name__}: {e}"
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
                logger.exception("LLM Judge: job worker error")
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
