"""Executes one MetricSpec against one target and persists the run's aggregate.

Scores and thresholds are reported the way the user reads them: for a "lower"
metric (Hallucination, Bias, Toxicity, PII Leakage) the score is a violation
rate and the threshold a maximum. DeepEval gets the matching minimum
(see metrics_catalog.deepeval_threshold) and decides pass/fail itself.
"""
from __future__ import annotations

import datetime as _dt
import sqlite3

from backend import storage
from backend.metrics_catalog import DEFAULT_THEME


class RunCancelled(Exception):
    """Raised from on_progress to stop a sweep between steps."""


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _direction(spec) -> str:
    return getattr(spec, "direction", "higher")


def _threshold(spec, threshold: float | None) -> float:
    """The pass mark for this run as the user reads it: theirs, else the catalog default."""
    return spec.threshold if threshold is None else threshold


def _deepeval_threshold(spec, threshold: float) -> float:
    return threshold if _direction(spec) == "higher" else round(1 - threshold, 6)


def _reported(spec, score: float | None) -> float | None:
    if score is None or _direction(spec) == "higher":
        return score
    return round(1 - score, 6)


def _context_used(spec, item: dict, retrieval: list[str] | None) -> tuple[list[str] | None, str | None]:
    """The context the metric reads, and where it came from ("chatbot" or "golden")."""
    fields = getattr(spec, "scores_on", ())
    if "retrieval_context" in fields:
        if retrieval:
            return retrieval, "chatbot"
        # The chatbot returned no retrieved context: score the golden's reference context.
        return (item.get("context") or None), "golden"
    if "context" in fields:
        return (item.get("context") or None), "golden"
    return None, None


GOLDEN_CONTEXT_NOTE = (
    "The chatbot's responses carry no retrieved context, so the retrieval context "
    "scored here is each golden's reference context. It checks the reference data, "
    "not the chatbot's retriever; set 'Retrieved context path' on a chatbot that "
    "returns its sources to score its own retrieval."
)


def _meets(spec, score: float | None, threshold: float) -> bool:
    if score is None:
        return False
    # Rounded so 0.7 set by the user is met by a 0.7000000001 average and vice versa.
    score = round(score, 6)
    return score <= threshold if _direction(spec) == "lower" else score >= threshold


def _transcript(turns: list[dict]) -> str:
    return "\n".join(f"{'User' if t['role'] == 'user' else 'Bot'}: {t['content']}" for t in turns)


def _run_conversation(spec, target, scenario: dict, report, done: int, total: int) -> tuple[object, list[dict]]:
    """Sends the scenario's user turns one by one, each with the conversation so far."""
    turns: list[dict] = []
    user_turns = scenario["user_turns"]
    for i, message in enumerate(user_turns):
        report(done, total, "chat", f"{scenario.get('name', 'conversation')} · turn {i + 1}/{len(user_turns)}")
        reply = target.chat(message, history=list(turns)).reply
        turns += [{"role": "user", "content": message}, {"role": "assistant", "content": reply}]
    return spec.build_case(scenario, turns), turns


def run_spec(
    spec,
    judge,
    target,
    target_id: int,
    conn: sqlite3.Connection,
    theme: str = DEFAULT_THEME,
    threshold: float | None = None,
    on_progress=None,
    persona: str = "",
    limit: int | None = None,
    probe_set: str = "",
) -> dict:
    """on_progress(done, total, phase, question) is called as the sweep moves:
    phase "chat" while the chatbot answers, "judge" while the judge scores.
    If it raises RunCancelled the sweep stops, and nothing is recorded.
    persona and probe_set go to spec.cases (security probes: the bot's role in
    the context, and which probe set to send); limit caps how many cases are sent."""
    report = on_progress or (lambda *_args: None)
    kind = getattr(spec, "kind", "single")
    try:
        items = spec.cases(theme=theme, persona=persona, probe_set=probe_set)
    except Exception as e:  # noqa: BLE001 - a broken dataset is a run error, not a crash
        return _error(spec, theme, f"{type(e).__name__}: {e}", cases_total=0)
    if not items:
        what = "conversation scenarios" if kind == "conversation" else "dataset"
        return _error(spec, theme, f"{what} for this metric is empty (theme {theme!r})", cases_total=0)
    available = len(items)
    if limit:
        items = items[:limit]

    pass_mark = _threshold(spec, threshold)
    prompt = getattr(spec, "prompt", None)
    rows = []
    try:
        metric = spec.build_metric(judge, _deepeval_threshold(spec, pass_mark))
        for done, item in enumerate(items):
            if kind == "conversation":
                case, turns = _run_conversation(spec, target, item, report, done, len(items))
                question, reply = item.get("name", "conversation"), _transcript(turns)
                sent = "\n".join(item.get("user_turns", []))
                expected, used, source = item.get("expected_outcome", ""), None, None
            else:
                question = item["question"]
                report(done, len(items), "chat", question)
                sent = prompt(item) if callable(prompt) else question
                answer = target.chat(sent)
                reply = answer.reply
                retrieval = getattr(answer, "retrieval_context", None)
                used, source = _context_used(spec, item, retrieval)
                expected = item.get("expected_answer", "")
                # The catalog's builders fall back to the golden's context themselves.
                case = spec.build_case(item, reply, retrieval)
            report(done, len(items), "judge", question)
            metric.measure(case)
            rows.append({
                "question": question,
                "input": sent,
                "actual_output": reply,
                "expected_output": expected,
                "context": used,
                "context_source": source,
                "score": _reported(spec, metric.score),
                "passed": bool(metric.is_successful()),
                "reason": metric.reason or "",
            })
    except RunCancelled:
        # A partial sweep would skew the trend, so a stopped run is not recorded.
        return {
            **_error(spec, theme, f"stopped after {len(rows)} of {len(items)} cases",
                     cases_total=len(items)),
            "status": "cancelled",
            "rows": rows,
            "cases_run": len(rows),
        }
    except Exception as e:  # noqa: BLE001 - surface any target/judge failure to the caller
        return _error(spec, theme, f"{type(e).__name__}: {e}", cases_total=len(items))

    scores = [r["score"] for r in rows if r["score"] is not None]
    avg = sum(scores) / len(scores) if scores else None
    # The run is judged on its average, like a quality gate; each case keeps its own verdict.
    passed = _meets(spec, avg, pass_mark)
    # One row per run: the charts plot run averages, not individual cases.
    storage.record_run(conn, target_id, spec.key, avg, passed, _now_iso(), cases_run=len(rows))
    return {
        "key": spec.key,
        "theme": theme,
        "status": "pass" if passed else "fail",
        "score": avg,
        "threshold": pass_mark,
        "direction": _direction(spec),
        "reason": next((r["reason"] for r in rows if not r["passed"]), rows[0]["reason"]),
        "rows": rows,
        "cases_run": len(rows),
        "cases_total": available,
        "error": None,
        "note": _golden_context_note(spec, rows),
    }


def _golden_context_note(spec, rows: list[dict]) -> str | None:
    fields = getattr(spec, "scores_on", ())
    if "retrieval_context" in fields and any(r["context_source"] == "golden" for r in rows):
        return GOLDEN_CONTEXT_NOTE
    return None


def judge_one(
    spec,
    judge,
    question: str,
    actual_output: str,
    expected_answer: str = "",
    context: list[str] | None = None,
    threshold: float | None = None,
) -> dict:
    """Scores one answer collected by hand. Nothing is persisted.

    The golden sweep reads its reference data from the dataset row; here it has
    to be supplied, so a metric that cannot score without it reports an error
    instead of quietly grading against an empty reference.
    """
    if getattr(spec, "kind", "single") == "conversation":
        return _error(spec, "", f"{spec.title} scores a whole conversation: run it with Run judge.",
                      cases_total=1)
    if getattr(spec, "dataset_name", "") == "security_probes":
        # Its rubric judges a reply to an attack, in the context of the bot's role.
        return _error(spec, "", f"{spec.title} scores replies to its red-team probes: run it from the dashboard.",
                      cases_total=1)
    item = {
        "question": question,
        "expected_answer": expected_answer,
        "context": context or [],
    }
    missing = [name for name in spec.needs if not item[name]]
    if missing:
        return _error(
            spec,
            "",
            f"{spec.title} also needs {', '.join(missing)}: ask one of your golden "
            f"questions, or add it to that golden answer.",
            cases_total=1,
        )

    pass_mark = _threshold(spec, threshold)
    try:
        metric = spec.build_metric(judge, _deepeval_threshold(spec, pass_mark))
        metric.measure(spec.build_case(item, actual_output, None))
        used, source = _context_used(spec, item, None)
    except Exception as e:  # noqa: BLE001 - surface any judge failure to the caller
        return _error(spec, "", f"{type(e).__name__}: {e}", cases_total=1)

    passed = bool(metric.is_successful())
    score = _reported(spec, metric.score)
    return {
        "key": spec.key,
        "theme": "",
        "status": "pass" if passed else "fail",
        "score": score,
        "threshold": pass_mark,
        "direction": _direction(spec),
        "reason": metric.reason or "",
        "rows": [{
            "question": question,
            "input": question,
            "actual_output": actual_output,
            "expected_output": expected_answer,
            "context": used,
            "context_source": source,
            "score": score,
            "passed": passed,
            "reason": metric.reason or "",
        }],
        "cases_run": 1,
        "cases_total": 1,
        "error": None,
        "note": _golden_context_note(spec, [{"context_source": source}]),
    }


def _error(spec, theme: str, message: str, cases_total: int) -> dict:
    return {
        "key": spec.key,
        "theme": theme,
        "status": "error",
        "score": None,
        "threshold": spec.threshold,
        "direction": _direction(spec),
        "reason": message,
        "rows": [],
        "cases_run": 0,
        "cases_total": cases_total,
        "error": message,
    }
