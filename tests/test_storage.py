import json
import sqlite3
import threading
from backend import storage


def test_init_db_creates_tables(tmp_path):
    conn = storage.init_db(str(tmp_path / "test.db"))
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    assert {"targets", "runs"} <= tables


def test_add_and_get_target(tmp_path):
    conn = storage.init_db(str(tmp_path / "test.db"))
    tid = storage.add_target(conn, "Bot", "http", {})
    row = storage.get_target(conn, tid)
    assert row["name"] == "Bot"
    assert row["type"] == "http"
    assert row["config"] == {}


def test_list_and_delete_target(tmp_path):
    conn = storage.init_db(str(tmp_path / "test.db"))
    tid = storage.add_target(conn, "T1", "http", {})
    assert len(storage.list_targets(conn)) == 1
    storage.delete_target(conn, tid)
    assert storage.list_targets(conn) == []


def test_record_and_query_runs(tmp_path):
    conn = storage.init_db(str(tmp_path / "test.db"))
    tid = storage.add_target(conn, "T1", "http", {})
    storage.record_run(conn, tid, "answer_relevancy", 0.9, True, "2026-09-16T10:00:00Z")
    storage.record_run(conn, tid, "answer_relevancy", 0.4, False, "2026-09-16T11:00:00Z")

    latest = storage.latest_runs(conn, tid)
    assert len(latest) == 1
    assert latest[0]["score"] == 0.4  # most recent per metric_key

    hist = storage.history(conn, tid, "answer_relevancy")
    assert [r["score"] for r in hist] == [0.9, 0.4]


def test_concurrent_access_on_shared_connection_is_safe(tmp_path):
    conn = storage.init_db(str(tmp_path / "test.db"))
    tid = storage.add_target(conn, "T1", "http", {})
    errors = []

    def worker(n):
        try:
            for i in range(50):
                storage.record_run(conn, tid, f"m{n}", i / 50, True, "2026-09-16T10:00:00Z")
                storage.history(conn, tid, f"m{n}")
                storage.latest_runs(conn, tid)
                storage.list_targets(conn)
        except Exception as e:  # noqa: BLE001 - any error means the lock failed
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(storage.history(conn, tid, "m0")) == 50


def test_init_migrates_legacy_http_targets_and_drops_retired_types(tmp_path):
    path = str(tmp_path / "test.db")
    conn = storage.init_db(path)
    flat = storage.add_target(conn, "Flat", "http", {
        "base_url": "http://bot.example/", "chat_path": "/chat", "message_field": "q",
        "response_path": "a", "headers": {"X-Key": "k"}, "theme": "t",
    })
    openai = storage.add_target(conn, "OpenAI", "http", {
        "base_url": "https://api.example", "chat_path": "/v1/chat/completions",
        "request_format": "openai_messages", "model": "m",
        "response_path": "choices.0.message.content",
    })
    mock = storage.add_target(conn, "Sample", "mock", {})
    storage.record_run(conn, mock, "m0", 0.9, True, "2026-01-01T00:00:00Z")
    conn.close()

    conn = storage.init_db(path)
    assert storage.get_target(conn, mock) is None
    assert storage.history(conn, mock, "m0") == []
    assert storage.get_target(conn, flat)["config"] == {
        "url": "http://bot.example/chat", "method": "POST",
        "headers": {"Content-Type": "application/json", "X-Key": "k"},
        "body_template": '{"q": "{{message}}"}', "response_path": "a", "theme": "t",
    }
    migrated = storage.get_target(conn, openai)["config"]
    assert migrated["url"] == "https://api.example/v1/chat/completions"
    assert json.loads(migrated["body_template"]) == {
        "messages": [{"role": "user", "content": "{{message}}"}], "model": "m",
    }


def test_runs_record_cases_run_and_legacy_dbs_gain_the_column(tmp_path):
    path = str(tmp_path / "legacy.db")
    legacy = sqlite3.connect(path)
    legacy.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, target_id INTEGER NOT NULL,"
                   " metric_key TEXT NOT NULL, score REAL, passed INTEGER NOT NULL, ts TEXT NOT NULL)")
    legacy.execute("INSERT INTO runs (target_id, metric_key, score, passed, ts) VALUES (1, 'm', 0.5, 1, 't')")
    legacy.commit()
    legacy.close()
    conn = storage.init_db(path)
    storage.record_run(conn, 1, "m", 0.9, True, "t2", cases_run=3)
    rows = storage.history(conn, 1, "m")
    assert [r["cases_run"] for r in rows] == [None, 3]


def test_documents_record_goldens_created_and_enhanced_path(tmp_path):
    conn = storage.init_db(str(tmp_path / "d.db"))
    doc_id = storage.add_document(conn, "t", "notes.txt")
    assert storage.get_document(conn, doc_id)["status"] == "queued"
    storage.set_document_status(conn, doc_id, "enhancing")
    storage.set_document_enhanced(conn, doc_id, "/x/1_notes_enhanced.txt")
    storage.set_document_status(conn, doc_id, "ready", goldens_created=6)
    doc = storage.get_document(conn, doc_id)
    assert (doc["status"], doc["goldens_created"], doc["enhanced_path"]) == ("ready", 6, "/x/1_notes_enhanced.txt")


def test_unfinished_documents_are_marked_interrupted_on_start(tmp_path):
    path = str(tmp_path / "d.db")
    conn = storage.init_db(path)
    busy = storage.add_document(conn, "t", "a.txt")
    storage.set_document_status(conn, busy, "generating")
    done = storage.add_document(conn, "t", "b.txt")
    storage.set_document_status(conn, done, "ready", goldens_created=2)
    conn.close()
    conn = storage.init_db(path)
    assert storage.get_document(conn, busy)["status"] == "error"
    assert "interrupted" in storage.get_document(conn, busy)["error"]
    assert storage.get_document(conn, done)["status"] == "ready"


def test_legacy_documents_table_gains_the_new_columns(tmp_path):
    path = str(tmp_path / "legacy.db")
    legacy = sqlite3.connect(path)
    legacy.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY AUTOINCREMENT, theme TEXT NOT NULL,"
                   " filename TEXT NOT NULL, uploaded_at TEXT NOT NULL, status TEXT NOT NULL, error TEXT)")
    legacy.execute("INSERT INTO documents (theme, filename, uploaded_at, status) VALUES ('t', 'a.txt', 'x', 'ready')")
    legacy.commit()
    legacy.close()
    conn = storage.init_db(path)
    doc = storage.list_documents(conn)[0]
    assert doc["goldens_created"] is None and doc["enhanced_path"] is None


def test_runs_record_how_they_were_judged(tmp_path):
    conn = storage.init_db(str(tmp_path / "t.db"))
    target_id = storage.add_target(conn, "bot", "http", {})
    storage.record_run(conn, target_id, "m", 0.8, True, "2026-01-01T00:00:00Z", cases_run=3,
                       judge_model="llama-x", judge_tokens=1200, judge_calls=6, target_calls=3,
                       duration_s=12.5, judge_spread=0.05)
    row = storage.history(conn, target_id, "m")[0]
    assert (row["judge_model"], row["judge_tokens"], row["judge_calls"], row["target_calls"],
            row["duration_s"], row["judge_spread"]) == ("llama-x", 1200, 6, 3, 12.5, 0.05)


def test_old_runs_table_gets_the_judging_columns(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, target_id INTEGER NOT NULL,"
                " metric_key TEXT NOT NULL, score REAL, passed INTEGER NOT NULL, ts TEXT NOT NULL)")
    old.execute("INSERT INTO runs (target_id, metric_key, score, passed, ts) VALUES (1, 'm', 0.5, 0, 't')")
    old.commit()
    old.close()
    conn = storage.init_db(path)
    row = storage.history(conn, 1, "m")[0]
    assert row["score"] == 0.5 and row["judge_model"] is None and row["judge_spread"] is None


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
