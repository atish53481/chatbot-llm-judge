from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend import storage
from backend.dashboard.runner import judge_one, run_spec
from tests.fakes import CannedChatbot


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
    def load(theme="general_support", **_kw):
        if seen_themes is not None:
            seen_themes.append(theme)
        return cases

    return SimpleNamespace(
        key="fake_metric",
        title="Fake Metric",
        threshold=0.7,
        needs=needs,
        cases=load,
        build_metric=lambda judge, threshold=0.7: metric,
        build_case=lambda g, reply, retrieval=None: SimpleNamespace(input=g["question"], actual_output=reply),
    )


def _case(question):
    return {"id": question, "theme": "general_support", "question": question,
            "expected_answer": "x", "context": []}


def _db(tmp_path):
    conn = storage.init_db(str(tmp_path / "t.db"))
    return conn, storage.add_target(conn, "bot", "http", {})


def test_run_spec_success_records_one_run_row(tmp_path):
    conn, target_id = _db(tmp_path)
    spec = _fake_spec([_case("What is your refund window?")], _fake_metric([0.9], [True]))
    result = run_spec(spec, judge=object(), target=CannedChatbot(), target_id=target_id, conn=conn)

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
    spec = _fake_spec([_case("q1"), _case("q2")], _fake_metric([0.9, 0.4], [True, False]))
    result = run_spec(spec, judge=object(), target=CannedChatbot(), target_id=target_id, conn=conn)

    assert result["status"] == "fail"  # average 0.65 is below 0.7
    assert result["score"] == pytest.approx(0.65)
    assert result["cases_run"] == 2
    assert result["reason"] == "reason 1"  # first failing case's reason
    history = storage.history(conn, target_id, "fake_metric")
    assert len(history) == 1
    assert history[0]["score"] == pytest.approx(0.65)
    assert history[0]["passed"] == 0


def test_run_passes_on_its_average_even_if_one_case_fails(tmp_path):
    conn, target_id = _db(tmp_path)
    spec = _fake_spec([_case("q1"), _case("q2")], _fake_metric([0.9, 0.5], [True, False]))
    result = run_spec(spec, judge=object(), target=CannedChatbot(), target_id=target_id, conn=conn)

    assert result["status"] == "pass"  # average 0.7 meets >= 0.7
    assert [r["passed"] for r in result["rows"]] == [True, False]


def test_lower_metric_passes_when_average_is_at_or_below(tmp_path):
    conn, target_id = _db(tmp_path)
    spec = _fake_spec([_case("q1"), _case("q2")], _fake_metric([1.0, 0.6], [True, False]))
    spec.direction = "lower"
    result = run_spec(spec, judge=object(), target=CannedChatbot(), target_id=target_id,
                      conn=conn, threshold=0.2)
    assert result["score"] == pytest.approx(0.2)  # violation rates 0.0 and 0.4
    assert result["status"] == "pass"


def test_run_spec_passes_theme_to_dataset(tmp_path):
    conn, target_id = _db(tmp_path)
    seen = []
    spec = _fake_spec([_case("q1")], _fake_metric([0.8], [True]), seen_themes=seen)
    result = run_spec(spec, judge=object(), target=CannedChatbot(),
                      target_id=target_id, conn=conn, theme="billing")
    assert seen == ["billing"]
    assert result["theme"] == "billing"


def test_run_spec_reports_error_on_empty_dataset(tmp_path):
    conn, target_id = _db(tmp_path)
    spec = _fake_spec([], _fake_metric([], []))
    result = run_spec(spec, judge=object(), target=CannedChatbot(), target_id=target_id, conn=conn)
    assert result["status"] == "error"
    assert result["error"].startswith("No goldens in theme")
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
        "input": "What is your refund window?",
        "actual_output": "Refunds within 7 business days.",
        "expected_output": "",
        "context": None,
        "context_source": None,
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
    spec.build_case = lambda g, reply, retrieval=None: seen.update(g) or SimpleNamespace()

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

    def broken(theme="general_support", **_kw):
        raise ValueError("bad goldens file")

    spec.cases = broken
    result = run_spec(spec, judge=object(), target=CannedChatbot(), target_id=target_id, conn=conn)
    assert result["status"] == "error"
    assert "bad goldens file" in result["error"]


def test_lower_metric_reports_violation_rate_and_flips_threshold(tmp_path):
    conn, target_id = _db(tmp_path)
    metric = _fake_metric([0.9], [True])  # DeepEval: 0.9 clean
    spec = _fake_spec([_case("q")], metric)
    spec.direction = "lower"
    spec.threshold = 0.5
    seen = []
    spec.build_metric = lambda judge, threshold=0.5: seen.append(threshold) or metric

    result = run_spec(spec, judge=object(), target=CannedChatbot(), target_id=target_id,
                      conn=conn, threshold=0.3)

    assert seen == [0.7]                   # "at most 0.3" = DeepEval minimum 0.7
    assert result["score"] == 0.1          # shown as a 10% violation rate
    assert result["threshold"] == 0.3 and result["direction"] == "lower"
    assert storage.history(conn, target_id, "fake_metric")[0]["score"] == 0.1


def test_conversation_metric_sends_turns_with_history(tmp_path):
    conn, target_id = _db(tmp_path)
    scenario = {"id": "c1", "name": "Order", "user_turns": ["I'm order 7.", "Which order am I?"]}
    spec = _fake_spec([scenario], _fake_metric([0.8], [True]))
    spec.kind = "conversation"
    built = {}
    spec.build_case = lambda s, turns: built.update(turns=turns) or SimpleNamespace()
    target = MagicMock()
    target.chat.side_effect = [SimpleNamespace(reply="Noted."), SimpleNamespace(reply="Order 7.")]

    result = run_spec(spec, judge=object(), target=target, target_id=target_id, conn=conn)

    assert result["status"] == "pass"
    first, second = target.chat.call_args_list
    assert first.kwargs["history"] == []
    assert second.kwargs["history"] == [{"role": "user", "content": "I'm order 7."},
                                        {"role": "assistant", "content": "Noted."}]
    assert [t["content"] for t in built["turns"]] == ["I'm order 7.", "Noted.", "Which order am I?", "Order 7."]
    assert result["rows"][0]["actual_output"].startswith("User: I'm order 7.\nBot: Noted.")


def test_retrieval_metric_falls_back_to_golden_context_and_says_so(tmp_path):
    conn, target_id = _db(tmp_path)
    golden = {**_case("q"), "expected_answer": "a", "context": ["golden fact"]}
    spec = _fake_spec([golden], _fake_metric([0.9], [True]))
    spec.needs_retrieval = True
    spec.scores_on = ("input", "expected_output", "retrieval_context")

    # The chatbot returns no retrieved context: the golden's context is scored, labelled.
    result = run_spec(spec, judge=object(), target=CannedChatbot(), target_id=target_id, conn=conn)
    assert result["status"] == "pass"
    row = result["rows"][0]
    assert row["context"] == ["golden fact"] and row["context_source"] == "golden"
    assert row["expected_output"] == "a" and row["input"] == "q"
    assert "reference context" in result["note"]

    # A chatbot that returns its sources is scored on them.
    target = MagicMock()
    target.chat.return_value = SimpleNamespace(reply="r", retrieval_context=["doc 1"])
    captured = {}
    spec.build_case = lambda g, reply, retrieval=None: captured.update(r=retrieval) or SimpleNamespace()
    second = _fake_metric([0.8], [True])
    spec.build_metric = lambda judge, threshold=0.7: second
    result = run_spec(spec, judge=object(), target=target, target_id=target_id, conn=conn)
    assert captured["r"] == ["doc 1"]
    assert result["rows"][0]["context_source"] == "chatbot" and result["note"] is None


def test_run_spec_limit_caps_cases_sent(tmp_path):
    conn, target_id = _db(tmp_path)
    cases = [_case(f"q{i}") for i in range(4)]
    spec = _fake_spec(cases, _fake_metric([0.9, 0.8], [True, True]))
    result = run_spec(spec, judge=object(), target=CannedChatbot(), target_id=target_id,
                      conn=conn, limit=2)
    assert result["cases_run"] == 2
    assert result["cases_total"] == 4


def test_run_spec_limit_above_count_runs_everything(tmp_path):
    conn, target_id = _db(tmp_path)
    spec = _fake_spec([_case("q0")], _fake_metric([0.9], [True]))
    result = run_spec(spec, judge=object(), target=CannedChatbot(), target_id=target_id,
                      conn=conn, limit=50)
    assert result["cases_run"] == 1 and result["status"] == "pass"


def test_run_spec_passes_persona_and_probe_set_to_cases(tmp_path):
    conn, target_id = _db(tmp_path)
    seen = []

    def load(theme="general_support", persona="", probe_set="ecommerce"):
        seen.append((persona, probe_set))
        return [_case("q")]

    spec = _fake_spec([], _fake_metric([0.9], [True]))
    spec.cases = load
    run_spec(spec, judge=object(), target=CannedChatbot(), target_id=target_id, conn=conn,
             persona="ShopEasy bot", probe_set="generic")
    assert seen == [("ShopEasy bot", "generic")]


def test_judge_one_rejects_security_metrics():
    spec = _fake_spec([], _fake_metric([], []))
    spec.dataset_name = "security_probes"
    result = judge_one(spec, judge=object(), question="hi", actual_output="hello")
    assert result["status"] == "error"
    assert "red-team probes" in result["error"]


def test_run_spec_stores_how_many_cases_ran(tmp_path):
    conn, target_id = _db(tmp_path)
    cases = [_case(f"q{i}") for i in range(3)]
    spec = _fake_spec(cases, _fake_metric([0.9], [True]))
    run_spec(spec, judge=object(), target=CannedChatbot(), target_id=target_id, conn=conn, limit=1)
    assert storage.latest_runs(conn, target_id)[0]["cases_run"] == 1


def test_run_spec_reports_a_case_note_from_the_spec(tmp_path):
    conn, target_id = _db(tmp_path)
    spec = _fake_spec([_case("short"), _case("long")], _fake_metric([0.9, 0.9], [True, True]))
    spec.case_note = lambda item: "input was shortened" if item["question"] == "long" else None
    result = run_spec(spec, judge=object(), target=CannedChatbot(), target_id=target_id, conn=conn)
    assert result["note"] == "input was shortened"

    spec.case_note = lambda item: None
    spec.build_metric = lambda judge, threshold=0.7: _fake_metric([0.9, 0.9], [True, True])
    result = run_spec(spec, judge=object(), target=CannedChatbot(), target_id=target_id, conn=conn)
    assert result["note"] is None


@pytest.mark.parametrize("dataset, kind, hint", [
    ("goldens_with_context", "single", "reference context"),
    ("goldens", "single", "No goldens"),
    ("conversations", "conversation", "No conversation scenarios"),
    ("security_probes", "single", "No security probes"),
])
def test_empty_dataset_error_says_what_is_missing(tmp_path, dataset, kind, hint):
    conn, target_id = _db(tmp_path)
    spec = _fake_spec([], _fake_metric([], []))
    spec.dataset_name, spec.kind = dataset, kind
    result = run_spec(spec, judge=object(), target=CannedChatbot(), target_id=target_id,
                      conn=conn, theme="generic")
    assert result["status"] == "error" and result["cases_total"] == 0
    assert hint in result["error"] and "'generic'" in result["error"]


class _LimitedBot(CannedChatbot):
    """Canned bot that, like a real one, refuses messages over its limit."""
    def __init__(self, limit):
        super().__init__()
        self.max_message_length = limit

    def chat(self, message, history=None):
        from backend.targets.http_client import MessageTooLong
        if len(message) > self.max_message_length:
            raise MessageTooLong(f"message is {len(message)} characters; limit {self.max_message_length}")
        return super().chat(message, history)


def test_cases_over_the_chatbots_limit_are_skipped_and_noted(tmp_path):
    conn, target_id = _db(tmp_path)
    spec = _fake_spec([_case("short?"), _case("x" * 50)], _fake_metric([0.9], [True]))
    result = run_spec(spec, judge=object(), target=_LimitedBot(20), target_id=target_id, conn=conn)
    assert result["status"] == "pass" and result["cases_run"] == 1
    assert "Skipped 1 of 2 cases" in result["note"] and "20" in result["note"]


def test_every_case_over_the_limit_is_an_error(tmp_path):
    conn, target_id = _db(tmp_path)
    spec = _fake_spec([_case("x" * 50)], _fake_metric([], []))
    result = run_spec(spec, judge=object(), target=_LimitedBot(20), target_id=target_id, conn=conn)
    assert result["status"] == "error" and "max message length" in result["error"]
    assert storage.history(conn, target_id, "fake_metric") == []


def test_service_message_stops_the_run_and_records_nothing(tmp_path):
    from backend.targets.reply_check import ChatbotUnavailable
    conn, target_id = _db(tmp_path)
    target = MagicMock()
    target.chat.side_effect = ChatbotUnavailable("Chatbot replied with a service message: 'limit reached'")
    spec = _fake_spec([_case("q1"), _case("q2")], _fake_metric([0.9, 0.9], [True, True]))
    result = run_spec(spec, judge=object(), target=target, target_id=target_id, conn=conn)
    assert result["status"] == "error" and "service message" in result["error"]
    assert target.chat.call_count == 1
    assert storage.history(conn, target_id, "fake_metric") == []


def test_summarization_source_is_cut_to_the_chatbots_limit(tmp_path):
    from backend.metrics_catalog import SPECS_BY_KEY
    spec = SPECS_BY_KEY["summarization"]
    item = {"question": "q", "expected_answer": "a", "context": ["word " * 400], "max_chars": 300}
    assert len(spec.prompt(item)) <= 300
    assert spec.build_case(item, "r").input in spec.prompt(item)
    assert spec.case_note(item)
