"""Executes one MetricSpec against one target and persists the run's aggregate."""
from __future__ import annotations

import datetime as _dt
import sqlite3

from backend import storage
from backend.metrics_catalog import DEFAULT_THEME


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_spec(
    spec,
    judge,
    target,
    target_id: int,
    conn: sqlite3.Connection,
    theme: str = DEFAULT_THEME,
) -> dict:
    try:
        items = spec.cases(theme=theme)
    except Exception as e:  # noqa: BLE001 - a broken dataset is a run error, not a crash
        return _error(spec, theme, f"{type(e).__name__}: {e}", cases_total=0)
    if not items:
        return _error(
            spec, theme, f"dataset for this metric is empty (theme {theme!r})", cases_total=0
        )

    rows = []
    try:
        metric = spec.build_metric(judge)
        for item in items:
            reply = target.chat(item["question"]).reply
            metric.measure(spec.build_case(item, reply))
            rows.append({
                "question": item["question"],
                "actual_output": reply,
                "score": metric.score,
                "passed": bool(metric.is_successful()),
                "reason": metric.reason or "",
            })
    except Exception as e:  # noqa: BLE001 - surface any target/judge failure to the caller
        return _error(spec, theme, f"{type(e).__name__}: {e}", cases_total=len(items))

    scores = [r["score"] for r in rows if r["score"] is not None]
    avg = sum(scores) / len(scores) if scores else None
    passed = all(r["passed"] for r in rows)
    # One row per run: the charts plot run averages, not individual cases.
    storage.record_run(conn, target_id, spec.key, avg, passed, _now_iso())
    return {
        "key": spec.key,
        "theme": theme,
        "status": "pass" if passed else "fail",
        "score": avg,
        "threshold": spec.threshold,
        "reason": next((r["reason"] for r in rows if not r["passed"]), rows[0]["reason"]),
        "rows": rows,
        "cases_run": len(rows),
        "cases_total": len(items),
        "error": None,
    }


def _error(spec, theme: str, message: str, cases_total: int) -> dict:
    return {
        "key": spec.key,
        "theme": theme,
        "status": "error",
        "score": None,
        "threshold": spec.threshold,
        "reason": message,
        "rows": [],
        "cases_run": 0,
        "cases_total": cases_total,
        "error": message,
    }
