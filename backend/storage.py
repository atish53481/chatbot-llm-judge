"""SQLite persistence for targets, judge run history and one-time setup flags.

FastAPI serves requests from a thread pool and all of them share one
connection, so every function holds the module lock: a sqlite3 connection
is not safe for concurrent use even with check_same_thread=False.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

_LOCK = threading.RLock()

SAMPLE_TARGET = ("Sample chatbot", "mock", {"theme": "general_support"})


def init_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    with _LOCK:
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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runs_target_metric ON runs (target_id, metric_key)"
        )
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.commit()
    return conn


def seed_sample_target_once(conn: sqlite3.Connection) -> None:
    """Adds the sample chatbot to a brand-new database, and never again.

    The seed is remembered in meta, so a user who deletes the sample does
    not get it back on the next start.
    """
    with _LOCK:
        if conn.execute("SELECT 1 FROM meta WHERE key = 'sample_seeded'").fetchone():
            return
        if conn.execute("SELECT COUNT(*) FROM targets").fetchone()[0] == 0:
            name, type_, config = SAMPLE_TARGET
            conn.execute(
                "INSERT INTO targets (name, type, config_json) VALUES (?, ?, ?)",
                (name, type_, json.dumps(config)),
            )
        conn.execute("INSERT INTO meta (key, value) VALUES ('sample_seeded', '1')")
        conn.commit()


def has_dom_session(conn: sqlite3.Connection, session_id: str) -> bool:
    """True when a web-page chatbot relays through the page host session_id."""
    with _LOCK:
        rows = conn.execute("SELECT config_json FROM targets WHERE type = 'dom'").fetchall()
    return any(json.loads(r["config_json"]).get("session_id") == session_id for r in rows)


def _target_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["type"],
        "config": json.loads(row["config_json"]),
    }


def add_target(conn: sqlite3.Connection, name: str, type_: str, config: dict) -> int:
    with _LOCK:
        cur = conn.execute(
            "INSERT INTO targets (name, type, config_json) VALUES (?, ?, ?)",
            (name, type_, json.dumps(config)),
        )
        conn.commit()
        return cur.lastrowid


def get_target(conn: sqlite3.Connection, target_id: int) -> dict[str, Any] | None:
    with _LOCK:
        row = conn.execute("SELECT * FROM targets WHERE id = ?", (target_id,)).fetchone()
    return None if row is None else _target_row(row)


def list_targets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    with _LOCK:
        rows = conn.execute("SELECT * FROM targets ORDER BY id").fetchall()
    return [_target_row(r) for r in rows]


def delete_target(conn: sqlite3.Connection, target_id: int) -> None:
    with _LOCK:
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
    with _LOCK:
        cur = conn.execute(
            "INSERT INTO runs (target_id, metric_key, score, passed, ts) VALUES (?, ?, ?, ?, ?)",
            (target_id, metric_key, score, int(passed), ts),
        )
        conn.commit()
        return cur.lastrowid


def latest_runs(conn: sqlite3.Connection, target_id: int) -> list[dict[str, Any]]:
    with _LOCK:
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
    with _LOCK:
        rows = conn.execute(
            "SELECT * FROM runs WHERE target_id = ? AND metric_key = ? ORDER BY id",
            (target_id, metric_key),
        ).fetchall()
    return [dict(r) for r in rows]
