import dataclasses
import json
import os

import pytest

from backend.datasets import goldens
from backend.judges import judge
from backend.metrics_catalog import ALL_SPECS, PASS_THRESHOLD, SPECS_BY_KEY


def test_all_specs_have_unique_keys():
    keys = [s.key for s in ALL_SPECS]
    assert len(keys) == len(set(keys))


def test_specs_by_key_matches_all_specs():
    assert set(SPECS_BY_KEY.keys()) == {s.key for s in ALL_SPECS}


def test_answer_relevancy_spec_builds_case():
    spec = SPECS_BY_KEY["answer_relevancy"]
    golden = {"question": "What is your refund window?", "expected_answer": "x", "context": []}
    case = spec.build_case(golden, "Refunds in 7 days.")
    assert case.input == "What is your refund window?"
    assert case.actual_output == "Refunds in 7 days."


def test_faithfulness_spec_requires_context():
    spec = SPECS_BY_KEY["faithfulness"]
    golden = {"question": "q", "expected_answer": "a", "context": ["ctx line"]}
    case = spec.build_case(golden, "reply")
    assert case.retrieval_context == ["ctx line"]


def test_expected_dataset_defaults_to_general_support_theme():
    spec = SPECS_BY_KEY["answer_relevancy"]
    cases = spec.cases()
    assert all(g["theme"] == "general_support" for g in cases)
    assert len(cases) >= 1


def test_cases_filters_by_given_theme(tmp_path, monkeypatch):
    """Test that cases() method filters by theme parameter."""
    goldens_data = [
        {
            "id": "g_other_1",
            "theme": "other",
            "question": "Question 1?",
            "expected_answer": "Answer 1",
            "context": ["Context line 1"],
            "categories": ["test"],
        },
        {
            "id": "g_other_2",
            "theme": "other",
            "question": "Question 2?",
            "expected_answer": "Answer 2",
            "context": [],
            "categories": ["test"],
        },
        {
            "id": "g_general_1",
            "theme": "general_support",
            "question": "Question 3?",
            "expected_answer": "Answer 3",
            "context": ["Context line 3"],
            "categories": ["test"],
        },
    ]

    path = tmp_path / "goldens.json"
    path.write_text(json.dumps(goldens_data))
    monkeypatch.setattr(goldens, "GOLDENS_PATH", str(path))

    # Test answer_relevancy (goldens dataset) filters by theme
    spec_ar = SPECS_BY_KEY["answer_relevancy"]
    other_cases = spec_ar.cases(theme="other")
    assert len(other_cases) == 2
    assert all(c["theme"] == "other" for c in other_cases)

    general_cases = spec_ar.cases(theme="general_support")
    assert len(general_cases) == 1
    assert general_cases[0]["theme"] == "general_support"

    # Test faithfulness (goldens_with_context dataset) filters by theme AND requires context
    spec_faith = SPECS_BY_KEY["faithfulness"]
    other_cases_faith = spec_faith.cases(theme="other")
    # Should only return rows with non-empty context
    assert len(other_cases_faith) == 1
    assert other_cases_faith[0]["id"] == "g_other_1"
    assert other_cases_faith[0]["context"] == ["Context line 1"]

    general_cases_faith = spec_faith.cases(theme="general_support")
    assert len(general_cases_faith) == 1


@pytest.mark.parametrize("spec", ALL_SPECS, ids=[s.key for s in ALL_SPECS])
def test_every_spec_builds_its_metric(monkeypatch, spec):
    """Test that every metric spec can build its metric without network calls."""
    monkeypatch.setenv("JUDGE_API_KEY", "fake-key")
    judge_instance = judge.build_judge()
    metric = spec.build_metric(judge_instance)
    assert metric.threshold == spec.threshold


def test_unknown_dataset_name_raises():
    spec = dataclasses.replace(SPECS_BY_KEY["answer_relevancy"], dataset_name="nope")
    with pytest.raises(ValueError, match="nope"):
        spec.cases()


def test_all_specs_share_the_pass_threshold():
    assert {s.threshold for s in ALL_SPECS} == {PASS_THRESHOLD}
