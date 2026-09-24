from types import SimpleNamespace

import pytest
from deepeval.models import LocalModel

from backend import usage
from backend.judges.judge import GroqJudge


@pytest.fixture(autouse=True)
def clean_counter():
    usage.reset()
    yield
    usage.reset()


def test_snapshot_starts_at_zero():
    assert usage.snapshot() == {
        "total_tokens": 0, "calls": 0, "target_calls": 0, "judge_calls": 0, "judge_tokens": 0,
    }


def test_records_judge_usage_object_and_target_calls():
    usage.record_judge(SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15))
    usage.record_judge({"prompt_tokens": 1, "completion_tokens": 2})
    usage.record_target()
    snap = usage.snapshot()
    assert snap["judge_calls"] == 2
    assert snap["judge_tokens"] == 18
    assert snap["target_calls"] == 1
    assert snap["calls"] == 3
    assert snap["total_tokens"] == 18


def test_missing_usage_still_counts_the_call():
    usage.record_judge(None)
    snap = usage.snapshot()
    assert snap["judge_calls"] == 1 and snap["judge_tokens"] == 0


def test_reset_clears_everything():
    usage.record_target()
    usage.reset()
    assert usage.snapshot()["calls"] == 0


def _fake_client(usage_obj):
    def create(**_kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="hello"))],
            usage=usage_obj,
        )
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _judge():
    return GroqJudge(model="m", api_key="k", base_url="http://127.0.0.1:9/v1", temperature=0.0, format="json")


def test_judge_generate_records_usage(monkeypatch):
    client = _fake_client(SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=10))
    monkeypatch.setattr(LocalModel, "load_model", lambda self, async_mode=False: client)
    _judge().generate("hi")
    snap = usage.snapshot()
    assert snap["judge_calls"] == 1 and snap["judge_tokens"] == 10


def test_judge_generate_without_usage_still_counts(monkeypatch):
    client = _fake_client(None)
    monkeypatch.setattr(LocalModel, "load_model", lambda self, async_mode=False: client)
    _judge().generate("hi")
    snap = usage.snapshot()
    assert snap["judge_calls"] == 1 and snap["judge_tokens"] == 0
