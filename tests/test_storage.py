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
