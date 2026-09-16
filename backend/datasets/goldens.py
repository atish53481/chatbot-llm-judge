"""JSON-backed golden dataset store — the 'golden rule' reference answers.

Single-user, single-process tool: read-modify-write the whole file under a
lock is enough, no database needed for this table.
"""
from __future__ import annotations

import json
import os
import threading
import uuid

GOLDENS_PATH = os.path.join(os.path.dirname(__file__), "goldens.json")
_LOCK = threading.Lock()


def _read_all() -> list[dict]:
    with open(GOLDENS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_all(rows: list[dict]) -> None:
    with open(GOLDENS_PATH, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)


def load_goldens(theme: str | None = None) -> list[dict]:
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


def delete_golden(golden_id: str) -> bool:
    with _LOCK:
        rows = _read_all()
        remaining = [r for r in rows if r["id"] != golden_id]
        if len(remaining) == len(rows):
            return False
        _write_all(remaining)
        return True
