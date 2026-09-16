"""Test answer quality: relevancy + correctness"""
import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase
from deepeval.metrics import AnswerRelevancyMetric, GEval
from deepeval.dataset import EvaluationDataset
from conftest import chatbot


# G-Eval custom correctness metric
correctness = GEval(
    name="Correctness",
    criteria="Determine whether the actual output accurately answers the question with correct information. Penalize invented details, wrong numbers, or contradictions.",
    evaluation_steps=[
        "Check if actual output directly addresses the input question",
        "Verify facts against expected output for accuracy",
        "Penalize any invented details or contradictions"
    ],
    threshold=0.7,
)

relevancy = AnswerRelevancyMetric(threshold=0.7)


def make_case(query: str, expected: str = None, context: list = None) -> LLMTestCase:
    """Query chatbot and build test case"""
    response = chatbot.answer(query, context)
    return LLMTestCase(
        input=query,
        actual_output=response["text"],
        expected_output=expected,
        retrieval_context=response.get("context", [])
    )


def test_store_hours():
    """Chatbot answers store hours question relevantly"""
    case = make_case("What are your store hours?")
    assert_test(case, [relevancy])


def test_return_policy():
    """Chatbot answers return policy question relevantly"""
    case = make_case("How do I return a product?")
    assert_test(case, [relevancy])


def test_student_discount():
    """Chatbot answers discount question relevantly"""
    case = make_case("Do you offer student discounts?")
    assert_test(case, [relevancy])


# Dataset-driven regression tests
import os
dataset = EvaluationDataset()
dataset_path = os.path.join(os.path.dirname(__file__), "datasets", "chatbot_golden.json")
dataset.add_test_cases_from_json_file(
    file_path=dataset_path,
    input_key_name="input",
    actual_output_key_name="expected_output",
    expected_output_key_name="expected_output",
    context_key_name="retrieval_context"
)


@pytest.mark.parametrize("case", dataset.test_cases, ids=lambda c: c.input[:50])
def test_golden_relevancy(case):
    """Test answer relevancy against golden dataset"""
    response = chatbot.answer(case.input, case.retrieval_context)
    case.actual_output = response["text"]
    assert_test(case, [relevancy])


@pytest.mark.parametrize("case", dataset.test_cases, ids=lambda c: c.input[:50])
def test_golden_correctness(case):
    """Test answer correctness against golden dataset"""
    response = chatbot.answer(case.input, case.retrieval_context)
    case.actual_output = response["text"]
    assert_test(case, [correctness])
