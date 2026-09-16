"""Live judge regression: every catalog metric against the mock target.

Spends judge tokens, so it only runs when asked:
    set RUN_LIVE_JUDGE=1 (with JUDGE_API_KEY in the environment or .env)
    python -m pytest -m live
"""
import os

import pytest

from backend.judges.judge import build_judge
from backend.metrics_catalog import ALL_SPECS
from backend.targets.mock import MockTargetClient

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
def test_mock_target_meets_threshold(spec, judge):
    # One golden per metric keeps a live run to seven judged answers.
    golden = spec.cases()[0]
    reply = MockTargetClient().chat(golden["question"]).reply
    metric = spec.build_metric(judge)
    metric.measure(spec.build_case(golden, reply))
    assert metric.is_successful(), f"{spec.key} scored {metric.score}: {metric.reason}"
