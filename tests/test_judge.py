import asyncio

import pytest

from backend.judges import judge as judge_module


def test_build_judge_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("JUDGE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="JUDGE_API_KEY"):
        judge_module.build_judge()


def test_build_judge_succeeds_with_api_key(monkeypatch):
    monkeypatch.setenv("JUDGE_API_KEY", "fake-key-for-construction-only")
    j = judge_module.build_judge()
    assert j is not None


def test_judge_name_reflects_env(monkeypatch):
    monkeypatch.setenv("JUDGE_MODEL", "openai/gpt-oss-120b")
    assert judge_module.judge_name() == "openai/gpt-oss-120b"


def test_async_generate_goes_through_the_locked_sync_path(monkeypatch):
    monkeypatch.setenv("JUDGE_API_KEY", "fake-key")
    judge_instance = judge_module.build_judge()
    calls = []

    def fake_generate(self, *args, **kwargs):
        calls.append((args, kwargs))
        return "ok", 0.0

    monkeypatch.setattr(judge_module.GroqJudge, "generate", fake_generate)
    assert asyncio.run(judge_instance.a_generate("prompt", schema=None)) == ("ok", 0.0)
    assert calls == [(("prompt",), {"schema": None})]
