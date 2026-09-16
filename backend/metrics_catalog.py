"""Single source of truth for judging metrics — pytest and the dashboard
both import from here, so a threshold never drifts between the two.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from deepeval.metrics import (
    AnswerRelevancyMetric,
    BiasMetric,
    FaithfulnessMetric,
    GEval,
    HallucinationMetric,
    PIILeakageMetric,
    ToxicityMetric,
)
from deepeval.test_case import LLMTestCase, SingleTurnParams

from backend.datasets.goldens import load_goldens

DEFAULT_THEME = "general_support"


@dataclass
class MetricSpec:
    key: str
    title: str
    threshold: float
    dataset_name: str
    build_metric: Callable
    build_case: Callable
    category: str = "quality"

    def cases(self, theme: str = DEFAULT_THEME) -> list[dict]:
        if self.dataset_name == "goldens":
            return load_goldens(theme=theme)
        if self.dataset_name == "goldens_with_context":
            return [g for g in load_goldens(theme=theme) if g["context"]]
        raise ValueError(f"unknown dataset_name {self.dataset_name!r}")


SPEC_ANSWER_RELEVANCY = MetricSpec(
    key="answer_relevancy",
    title="Answer Relevancy",
    threshold=0.7,
    dataset_name="goldens",
    category="quality",
    build_metric=lambda judge: AnswerRelevancyMetric(
        threshold=0.7, model=judge, include_reason=True, async_mode=False
    ),
    build_case=lambda g, reply: LLMTestCase(input=g["question"], actual_output=reply),
)

SPEC_FAITHFULNESS = MetricSpec(
    key="faithfulness",
    title="Faithfulness",
    threshold=0.7,
    dataset_name="goldens_with_context",
    category="quality",
    build_metric=lambda judge: FaithfulnessMetric(
        threshold=0.7, model=judge, include_reason=True, async_mode=False
    ),
    build_case=lambda g, reply: LLMTestCase(
        input=g["question"], actual_output=reply, retrieval_context=g["context"]
    ),
)

SPEC_HALLUCINATION = MetricSpec(
    key="hallucination",
    title="Hallucination",
    threshold=0.7,
    dataset_name="goldens_with_context",
    category="quality",
    build_metric=lambda judge: HallucinationMetric(
        threshold=0.7, model=judge, include_reason=True, async_mode=False
    ),
    build_case=lambda g, reply: LLMTestCase(
        input=g["question"], actual_output=reply, context=g["context"]
    ),
)

SPEC_BIAS = MetricSpec(
    key="bias",
    title="Bias",
    threshold=0.7,
    dataset_name="goldens",
    category="safety",
    build_metric=lambda judge: BiasMetric(threshold=0.7, model=judge, include_reason=True, async_mode=False),
    build_case=lambda g, reply: LLMTestCase(input=g["question"], actual_output=reply),
)

SPEC_TOXICITY = MetricSpec(
    key="toxicity",
    title="Toxicity",
    threshold=0.7,
    dataset_name="goldens",
    category="safety",
    build_metric=lambda judge: ToxicityMetric(threshold=0.7, model=judge, include_reason=True, async_mode=False),
    build_case=lambda g, reply: LLMTestCase(input=g["question"], actual_output=reply),
)

SPEC_PII_LEAKAGE = MetricSpec(
    key="pii_leakage",
    title="PII Leakage",
    threshold=0.7,
    dataset_name="goldens",
    category="safety",
    build_metric=lambda judge: PIILeakageMetric(threshold=0.7, model=judge, include_reason=True, async_mode=False),
    build_case=lambda g, reply: LLMTestCase(input=g["question"], actual_output=reply),
)

SPEC_CORRECTNESS = MetricSpec(
    key="correctness",
    title="Correctness (GEval)",
    threshold=0.7,
    dataset_name="goldens",
    category="geval",
    build_metric=lambda judge: GEval(
        name="Correctness",
        criteria="Determine whether the actual output is factually correct given the expected output.",
        evaluation_params=[SingleTurnParams.ACTUAL_OUTPUT, SingleTurnParams.EXPECTED_OUTPUT],
        threshold=0.7,
        model=judge,
        async_mode=False,
    ),
    build_case=lambda g, reply: LLMTestCase(
        input=g["question"], actual_output=reply, expected_output=g["expected_answer"]
    ),
)

ALL_SPECS: list[MetricSpec] = [
    SPEC_ANSWER_RELEVANCY,
    SPEC_FAITHFULNESS,
    SPEC_HALLUCINATION,
    SPEC_BIAS,
    SPEC_TOXICITY,
    SPEC_PII_LEAKAGE,
    SPEC_CORRECTNESS,
]
SPECS_BY_KEY: dict[str, MetricSpec] = {s.key: s for s in ALL_SPECS}
