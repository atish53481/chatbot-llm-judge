"""SQLite persistence for targets and judge run history.

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
        conn.commit()
    return conn


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
