"""Executes one MetricSpec against one target and persists the run's aggregate.

Scores and thresholds are reported the way the user reads them: for a "lower"
metric (Hallucination, Bias, Toxicity, PII Leakage) the score is a violation
rate and the threshold a maximum. DeepEval gets the matching minimum
(see metrics_catalog.deepeval_threshold) and decides pass/fail itself.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
import time

from backend import storage, usage
from backend.metrics_catalog import DEFAULT_THEME
from backend.targets.http_client import MessageTooLong


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


NEEDS_OWN_SOURCES = (
    "Not applicable: this metric scores how the chatbot cites its own sources, and this "
    "chatbot returns none. Set 'Retrieved context path' on a chatbot whose responses list "
    "the documents it used."
)

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
    check_consistency: bool = False,
) -> dict:
    """on_progress(done, total, phase, question) is called as the sweep moves:
    phase "chat" while the chatbot answers, "judge" while the judge scores.
    If it raises RunCancelled the sweep stops, and nothing is recorded.
    persona and probe_set go to spec.cases (security probes: the bot's role in
    the context, and which probe set to send); limit caps how many cases are sent.
    check_consistency has the judge score every reply twice (the chatbot is
    asked once): each case scores the average, and the run reports the widest
    gap between the two scorings, flagged when it passes UNSTABLE_SPREAD."""
    usage_before, started = usage.snapshot(), time.monotonic()
    report = on_progress or (lambda *_args: None)
    kind = getattr(spec, "kind", "single")
    if getattr(spec, "needs_own_sources", False) and not getattr(target, "context_path", ""):
        return _error(spec, theme, NEEDS_OWN_SOURCES, cases_total=0)
    try:
        items = spec.cases(theme=theme, persona=persona, probe_set=probe_set)
    except Exception as e:  # noqa: BLE001 - a broken dataset is a run error, not a crash
        return _error(spec, theme, f"{type(e).__name__}: {e}", cases_total=0)
    if not items:
        return _error(spec, theme, _empty_dataset_message(spec, theme, probe_set), cases_total=0)
    available = len(items)
    if limit:
        items = items[:limit]
    # The chatbot's max message length: prompts that can shrink (Summarization's
    # source) are cut to it, and cases still too long are skipped, not sent.
    max_len = getattr(target, "max_message_length", None)
    if max_len and kind != "conversation":
        items = [{**item, "max_chars": max_len} for item in items]

    pass_mark = _threshold(spec, threshold)
    prompt = getattr(spec, "prompt", None)
    rows, scored, skipped, spreads = [], [], 0, []
    try:
        metric = spec.build_metric(judge, _deepeval_threshold(spec, pass_mark))
        for done, item in enumerate(items):
            try:
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
            except MessageTooLong:
                skipped += 1
                continue
            scored.append(item)
            report(done, len(items), "judge", question)
            metric.measure(case)
            score, passed_case, reason = _reported(spec, metric.score), bool(metric.is_successful()), metric.reason or ""
            row_scores = None
            if check_consistency:
                metric.measure(case)
                row_scores = [score, _reported(spec, metric.score)]
                both = [s for s in row_scores if s is not None]
                if both:
                    score = sum(both) / len(both)
                    passed_case = _meets(spec, score, pass_mark)
                if None not in row_scores:
                    spreads.append(abs(row_scores[0] - row_scores[1]))
            rows.append({
                "question": question,
                "input": sent,
                "actual_output": reply,
                "expected_output": expected,
                "context": used,
                "context_source": source,
                "score": score,
                "passed": passed_case,
                "reason": reason,
                **({"scores": row_scores} if row_scores is not None else {}),
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
    if not rows:
        return _error(
            spec, theme,
            f"All {skipped} cases are longer than this chatbot's max message length "
            f"({max_len} characters), so nothing was sent. Raise the limit if the chatbot "
            "accepts longer messages.",
            cases_total=len(items),
        )

    scores = [r["score"] for r in rows if r["score"] is not None]
    avg = sum(scores) / len(scores) if scores else None
    # The run is judged on its average, like a quality gate; each case keeps its own verdict.
    passed = _meets(spec, avg, pass_mark)
    # How this run was judged: counted as the change in the usage counters
    # (runs go one at a time, so nothing else moves them meanwhile).
    usage_after = usage.snapshot()
    judge_model = _judge_name(judge)
    judge_tokens = usage_after["judge_tokens"] - usage_before["judge_tokens"]
    judge_calls = usage_after["judge_calls"] - usage_before["judge_calls"]
    chatbot_calls = usage_after["target_calls"] - usage_before["target_calls"]
    duration = round(time.monotonic() - started, 2)
    spread = max(spreads) if spreads else None
    previous = storage.history(conn, target_id, spec.key)
    previous_model = previous[-1].get("judge_model") if previous else None
    result = {
        "cases_skipped": skipped,
        "judge": {"model": judge_model, "tokens": judge_tokens, "calls": judge_calls},
        "chatbot_calls": chatbot_calls,
        "duration_s": duration,
        "judge_spread": spread,
        "judge_unstable": spread is not None and spread > UNSTABLE_SPREAD,
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
        "note": _join_notes(
            _golden_context_note(spec, rows),
            _case_note(spec, scored),
            f"Skipped {skipped} of {len(items)} cases longer than this chatbot's max message "
            f"length ({max_len} characters)." if skipped else None,
            f"Judge model changed from {previous_model} to {judge_model}: scores may not be "
            "comparable with earlier runs."
            if previous_model and judge_model and previous_model != judge_model else None,
            f"Judge unstable: two scorings of one case differed by {spread:.2f} (over "
            f"{UNSTABLE_SPREAD}), so treat this score with care."
            if spread is not None and spread > UNSTABLE_SPREAD else None,
        ),
    }
    # One row per run: the charts plot run averages, not individual cases; the
    # full result lets Details and the report show the cases from any browser.
    storage.record_run(
        conn, target_id, spec.key, avg, passed, _now_iso(), cases_run=len(rows),
        judge_model=judge_model, judge_tokens=judge_tokens, judge_calls=judge_calls,
        target_calls=chatbot_calls, duration_s=duration, judge_spread=spread,
        cases_skipped=skipped, result_json=json.dumps(result, default=str),
    )
    return result


# Two scorings of one reply further apart than this flag the run as unstable.
UNSTABLE_SPREAD = 0.15


def _judge_name(judge) -> str | None:
    """The judge model's name (DeepEval models expose get_model_name)."""
    try:
        name = judge.get_model_name()
    except Exception:  # noqa: BLE001 - a judge without a name is still a judge
        return None
    return str(name) if name else None


def _join_notes(*notes: str | None) -> str | None:
    return " ".join(n for n in notes if n) or None


def _golden_context_note(spec, rows: list[dict]) -> str | None:
    fields = getattr(spec, "scores_on", ())
    if "retrieval_context" in fields and any(r["context_source"] == "golden" for r in rows):
        return GOLDEN_CONTEXT_NOTE
    return None


def _empty_dataset_message(spec, theme: str, probe_set: str) -> str:
    """Says what the metric needs and where to add it, for a theme with no cases."""
    dataset = getattr(spec, "dataset_name", "")
    if getattr(spec, "kind", "single") == "conversation" or dataset == "conversations":
        return (f"No conversation scenarios in theme {theme!r}. "
                "Add one under Conversation scenarios in the side panel.")
    if dataset == "security_probes":
        return (f"No security probes for this metric in probe set {probe_set!r} (theme {theme!r}). "
                "Add one in the side panel.")
    if dataset == "goldens_with_context":
        return (f"No goldens with reference context in theme {theme!r}. "
                "Generate goldens from the chatbot's docs or help page to use this metric.")
    return f"No goldens in theme {theme!r}. Add some in the side panel."


def _case_note(spec, items: list[dict]) -> str | None:
    note = getattr(spec, "case_note", None)
    if not callable(note):
        return None
    return next((n for n in map(note, items) if n), None)


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
        "note": _golden_context_note(spec, [{"context_source": source}]) or _case_note(spec, [item]),
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
