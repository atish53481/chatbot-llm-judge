"""Live judge regression: every catalog metric against the canned test chatbot.

Spends judge tokens, so it only runs when asked:
    set RUN_LIVE_JUDGE=1 (with JUDGE_API_KEY in the environment or .env)
    python -m pytest -m live
"""
import os

import pytest

from backend.judges.judge import build_judge
from backend.metrics_catalog import ALL_SPECS, deepeval_threshold
from tests.fakes import CannedChatbot

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_JUDGE") != "1" or not os.getenv("JUDGE_API_KEY"),
        reason="live judge tests need RUN_LIVE_JUDGE=1 and JUDGE_API_KEY",
    ),
]


@pytest.fixture(scope="module")
def judge():
    return build_judge()


@pytest.mark.parametrize("spec", ALL_SPECS, ids=[s.key for s in ALL_SPECS])
def test_canned_chatbot_meets_threshold(spec, judge):
    if spec.kind == "conversation" or spec.needs_retrieval:
        pytest.skip("the canned chatbot keeps no conversation and returns no retrieved context")
    # One golden per metric keeps a live run to one judged answer per metric.
    golden = spec.cases()[0]
    reply = CannedChatbot().chat(spec.prompt(golden)).reply
    metric = spec.build_metric(judge, deepeval_threshold(spec, spec.threshold))
    metric.measure(spec.build_case(golden, reply))
    assert metric.is_successful(), f"{spec.key} scored {metric.score}: {metric.reason}"
