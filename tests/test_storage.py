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


def test_concurrent_access_on_shared_connection_is_safe(tmp_path):
    conn = storage.init_db(str(tmp_path / "test.db"))
    tid = storage.add_target(conn, "T1", "mock", {})
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


def test_sample_target_is_seeded_once(tmp_path):
    conn = storage.init_db(str(tmp_path / "test.db"))
    storage.seed_sample_target_once(conn)
    targets = storage.list_targets(conn)
    assert [(t["name"], t["type"]) for t in targets] == [("Sample chatbot", "mock")]
    storage.delete_target(conn, targets[0]["id"])
    storage.seed_sample_target_once(conn)
    assert storage.list_targets(conn) == []  # a deleted sample stays deleted


def test_seed_skips_databases_that_already_have_targets(tmp_path):
    conn = storage.init_db(str(tmp_path / "test.db"))
    storage.add_target(conn, "Mine", "mock", {})
    storage.seed_sample_target_once(conn)
    assert [t["name"] for t in storage.list_targets(conn)] == ["Mine"]


def test_has_dom_session(tmp_path):
    conn = storage.init_db(str(tmp_path / "test.db"))
    storage.add_target(conn, "Web bot", "dom", {"session_id": "shop.example"})
    storage.add_target(conn, "Api bot", "http", {"session_id": "api.example"})
    assert storage.has_dom_session(conn, "shop.example")
    assert not storage.has_dom_session(conn, "api.example")
    assert not storage.has_dom_session(conn, "other.example")
