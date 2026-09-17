from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend import storage
from backend.dashboard.runner import judge_one, run_spec
from backend.targets.mock import MockTargetClient


def _fake_metric(scores, passes):
    """Metric double: each measure() call takes the next score/pass pair."""
    metric = MagicMock()
    state = {"i": -1}

    def measure(case):
        state["i"] += 1
        metric.score = scores[state["i"]]
        metric.reason = f"reason {state['i']}"

    metric.measure.side_effect = measure
    metric.is_successful.side_effect = lambda: passes[state["i"]]
    return metric


def _fake_spec(cases, metric, seen_themes=None, needs=()):
    def load(theme="general_support"):
        if seen_themes is not None:
            seen_themes.append(theme)
        return cases

    return SimpleNamespace(
        key="fake_metric",
        title="Fake Metric",
        threshold=0.7,
        needs=needs,
        cases=load,
        build_metric=lambda judge: metric,
        build_case=lambda g, reply: SimpleNamespace(input=g["question"], actual_output=reply),
    )


def _case(question):
    return {"id": question, "theme": "general_support", "question": question,
            "expected_answer": "x", "context": []}


def _db(tmp_path):
    conn = storage.init_db(str(tmp_path / "t.db"))
    return conn, storage.add_target(conn, "mock", "mock", {})


def test_run_spec_success_records_one_run_row(tmp_path):
    conn, target_id = _db(tmp_path)
    spec = _fake_spec([_case("What is your refund window?")], _fake_metric([0.9], [True]))
    result = run_spec(spec, judge=object(), target=MockTargetClient(), target_id=target_id, conn=conn)

    assert result["status"] == "pass"
    assert result["score"] == 0.9
    assert result["cases_run"] == 1
    assert result["error"] is None
    assert "7 business days" in result["rows"][0]["actual_output"]

    history = storage.history(conn, target_id, "fake_metric")
    assert len(history) == 1
    assert history[0]["score"] == 0.9
    assert history[0]["passed"] == 1


def test_run_spec_records_average_of_all_cases_as_one_row(tmp_path):
    conn, target_id = _db(tmp_path)
    spec = _fake_spec([_case("q1"), _case("q2")], _fake_metric([0.9, 0.5], [True, False]))
    result = run_spec(spec, judge=object(), target=MockTargetClient(), target_id=target_id, conn=conn)

    assert result["status"] == "fail"
    assert result["score"] == pytest.approx(0.7)
    assert result["cases_run"] == 2
    assert result["reason"] == "reason 1"  # first failing case's reason
    history = storage.history(conn, target_id, "fake_metric")
    assert len(history) == 1
    assert history[0]["score"] == pytest.approx(0.7)
    assert history[0]["passed"] == 0


def test_run_spec_passes_theme_to_dataset(tmp_path):
    conn, target_id = _db(tmp_path)
    seen = []
    spec = _fake_spec([_case("q1")], _fake_metric([0.8], [True]), seen_themes=seen)
    result = run_spec(spec, judge=object(), target=MockTargetClient(),
                      target_id=target_id, conn=conn, theme="billing")
    assert seen == ["billing"]
    assert result["theme"] == "billing"


def test_run_spec_reports_error_on_empty_dataset(tmp_path):
    conn, target_id = _db(tmp_path)
    spec = _fake_spec([], _fake_metric([], []))
    result = run_spec(spec, judge=object(), target=MockTargetClient(), target_id=target_id, conn=conn)
    assert result["status"] == "error"
    assert "empty" in result["error"]
    assert storage.history(conn, target_id, "fake_metric") == []


def test_run_spec_reports_error_on_target_exception_and_records_nothing(tmp_path):
    conn, target_id = _db(tmp_path)
    spec = _fake_spec([_case("q1")], _fake_metric([0.9], [True]))
    broken_target = MagicMock()
    broken_target.chat.side_effect = ConnectionError("target unreachable")

    result = run_spec(spec, judge=object(), target=broken_target, target_id=target_id, conn=conn)
    assert result["status"] == "error"
    assert "unreachable" in result["error"]
    assert result["cases_total"] == 1
    assert storage.history(conn, target_id, "fake_metric") == []


def test_judge_one_scores_a_single_answer():
    spec = _fake_spec([], _fake_metric([0.42], [False]))
    result = judge_one(spec, judge=object(), question="What is your refund window?",
                       actual_output="Refunds within 7 business days.")

    assert result["status"] == "fail"
    assert result["score"] == 0.42
    assert result["cases_run"] == 1
    assert result["error"] is None
    assert result["rows"] == [{
        "question": "What is your refund window?",
        "actual_output": "Refunds within 7 business days.",
        "score": 0.42,
        "passed": False,
        "reason": "reason 0",
    }]


def test_judge_one_reports_a_missing_required_input():
    spec = _fake_spec([], _fake_metric([], []), needs=("expected_answer",))
    result = judge_one(spec, judge=object(), question="q", actual_output="a")

    assert result["status"] == "error"
    assert "expected_answer" in result["error"]
    assert result["cases_run"] == 0
    assert "score" in result and result["score"] is None


def test_judge_one_hands_reference_data_to_the_case():
    seen = {}
    spec = _fake_spec([], _fake_metric([1.0], [True]), needs=("context", "expected_answer"))
    spec.build_case = lambda g, reply: seen.update(g) or SimpleNamespace()

    judge_one(spec, judge=object(), question="q", actual_output="a",
              expected_answer="the golden answer", context=["a fact"])

    assert seen["question"] == "q"
    assert seen["expected_answer"] == "the golden answer"
    assert seen["context"] == ["a fact"]


def test_judge_one_reports_a_judge_failure():
    spec = _fake_spec([], _fake_metric([], []))
    spec.build_metric = MagicMock(side_effect=RuntimeError("judge exploded"))

    result = judge_one(spec, judge=object(), question="q", actual_output="a")

    assert result["status"] == "error"
    assert "judge exploded" in result["error"]


def test_run_spec_reports_error_when_dataset_fails_to_load(tmp_path):
    conn, target_id = _db(tmp_path)
    spec = _fake_spec([], _fake_metric([], []))

    def broken(theme="general_support"):
        raise ValueError("bad goldens file")

    spec.cases = broken
    result = run_spec(spec, judge=object(), target=MockTargetClient(), target_id=target_id, conn=conn)
    assert result["status"] == "error"
    assert "bad goldens file" in result["error"]
