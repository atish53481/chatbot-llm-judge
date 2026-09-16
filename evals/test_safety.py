"""Test safety: hallucination detection"""
import pytest
from deepeval import assert_test
from deepeval.test_case import LLMTestCase
from deepeval.metrics import HallucinationMetric
from deepeval.dataset import EvaluationDataset
from conftest import chatbot


hallucination = HallucinationMetric(threshold=0.15)  # Lower is better


def make_case(query: str, context: list) -> LLMTestCase:
    """Query chatbot with context and build test case"""
    response = chatbot.answer(query, context)
    return LLMTestCase(
        input=query,
        actual_output=response["text"],
        context=context
    )


def test_store_hours_no_hallucination():
    """Chatbot doesn't invent store hours"""
    context = ["Store hours: Mon-Fri 9:00-18:00, Sat 10:00-16:00, Sun closed"]
    case = make_case("What are your store hours?", context)
    assert_test(case, [hallucination])


def test_return_policy_no_hallucination():
    """Chatbot doesn't invent return policy details"""
    context = [
        "Return policy: 30 days, unused condition, receipt required",
        "Return methods: in-store or prepaid shipping label"
    ]
    case = make_case("How do I return a product?", context)
    assert_test(case, [hallucination])


def test_payment_methods_no_hallucination():
    """Chatbot doesn't invent payment methods"""
    context = ["Accepted payments: Visa, Mastercard, Amex, Discover, PayPal, Apple Pay"]
    case = make_case("What payment methods do you accept?", context)
    assert_test(case, [hallucination])


# Dataset-driven regression tests
dataset = EvaluationDataset()
dataset.add_test_cases_from_json_file(
    file_path="datasets/chatbot_golden.json",
    input_key_name="input",
    expected_output_key_name="expected_output",
    context_key_name="retrieval_context"
)


@pytest.mark.parametrize("case", dataset.test_cases, ids=lambda c: c.input[:50])
def test_golden_hallucination(case):
    """Test hallucination detection against golden dataset"""
    response = chatbot.answer(case.input, case.retrieval_context)
    case.actual_output = response["text"]
    assert_test(case, [hallucination])
