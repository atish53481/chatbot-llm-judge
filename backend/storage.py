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
