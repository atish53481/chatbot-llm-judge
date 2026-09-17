"""JSON-backed golden dataset store — the 'golden rule' reference answers.

Single-user tool: read-modify-write the whole file under a lock. Reads take
the lock too (FastAPI serves requests from several threads), and writes go
to a temp file that then replaces the original, so a crash mid-write can
never truncate the user's hand-written goldens.
"""
from __future__ import annotations

import json
import os
import threading
import uuid

GOLDENS_PATH = os.path.join(os.path.dirname(__file__), "goldens.json")
_LOCK = threading.RLock()


def _read_all() -> list[dict]:
    with open(GOLDENS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_all(rows: list[dict]) -> None:
    tmp_path = GOLDENS_PATH + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, GOLDENS_PATH)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def load_goldens(theme: str | None = None) -> list[dict]:
    with _LOCK:
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


def update_golden(
    golden_id: str,
    theme: str,
    question: str,
    expected_answer: str,
    context: list[str] | None = None,
    categories: list[str] | None = None,
) -> dict | None:
    """Replaces one row's fields in place, keeping its id. None when unknown."""
    with _LOCK:
        rows = _read_all()
        for row in rows:
            if row["id"] == golden_id:
                row.update({
                    "theme": theme,
                    "question": question,
                    "expected_answer": expected_answer,
                    "context": context or [],
                    "categories": categories or [],
                })
                _write_all(rows)
                return dict(row)
        return None


def delete_golden(golden_id: str) -> bool:
    with _LOCK:
        rows = _read_all()
        remaining = [r for r in rows if r["id"] != golden_id]
        if len(remaining) == len(rows):
            return False
        _write_all(remaining)
        return True
