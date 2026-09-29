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
