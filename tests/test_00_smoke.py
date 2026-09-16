"""Wiring checks that cost nothing — run these before any judge-token test."""
import pytest

from backend.targets.mock import MockTargetClient


@pytest.mark.smoke
def test_mock_target_is_reachable():
    assert MockTargetClient().health()["status"] == "ok"


@pytest.mark.smoke
def test_mock_target_answers_a_known_question():
    reply = MockTargetClient().chat("What is your refund window?")
    assert reply.reply.strip()
    assert reply.mode == "mock"


@pytest.mark.smoke
def test_judge_api_key_env_var_name_is_consistent():
    """Guards against renaming JUDGE_API_KEY in one file but not another."""
    from backend.judges import judge as judge_module
    assert judge_module.JUDGE_API_KEY_ENV == "JUDGE_API_KEY"


@pytest.mark.smoke
def test_default_golden_theme_is_seeded():
    from backend.datasets.goldens import load_goldens
    assert len(load_goldens(theme="general_support")) >= 1
