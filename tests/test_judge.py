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
