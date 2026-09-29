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

# The canned bot reproduces a golden's expected answer, so the live run uses the
# sample chatbot's factual set: its answers are literal replies, unlike the
# generic set's behaviour-style goldens, which describe what a good answer does.
# Security metrics ignore the theme and use the shipped probe set.
LIVE_THEME = "general_support"

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
def test_real_judge_scores_every_metric(spec, judge):
    if spec.kind == "conversation" or spec.needs_retrieval:
        pytest.skip("the canned chatbot keeps no conversation and returns no retrieved context")
    # The bot and the cases share one theme, so the canned answers line up with
    # the questions it is asked.
    cases = spec.cases(theme=LIVE_THEME)
    if not cases:
        pytest.skip(f"the shipped {LIVE_THEME} dataset has no case for {spec.key}")
    # One case per metric keeps a live run to one judged answer per metric.
    golden = cases[0]
    reply = CannedChatbot(theme=LIVE_THEME).chat(spec.prompt(golden)).reply
    assert reply.strip(), f"{spec.key}: the chatbot under test gave no reply"
    metric = spec.build_metric(judge, deepeval_threshold(spec, spec.threshold))
    metric.measure(spec.build_case(golden, reply))
    # What this pins is the integration: the real judge was reachable, the metric
    # built, and it returned a usable score and reason. Whether a cheap judge
    # model happens to pass the bot is its opinion and is not stable enough to
    # assert (the same answer can score 0 then 1 — see "Check judge consistency"
    # in the README). The offline suite pins the pass/fail logic deterministically.
    score = metric.score
    assert isinstance(score, int | float) and 0.0 <= float(score) <= 1.0, spec.key
    assert (metric.reason or "").strip(), spec.key
