from backend.targets.mock import MockTargetClient


def test_mock_health_reports_ok():
    client = MockTargetClient()
    assert client.health() == {"status": "ok"}


def test_mock_matches_known_question():
    client = MockTargetClient()
    reply = client.chat("What is your refund window?")
    assert "7 business days" in reply.reply
    assert reply.mode == "mock"
    assert reply.model == "mock-canned"


def test_mock_falls_back_for_unknown_question():
    client = MockTargetClient()
    reply = client.chat("What color is the sky on Mars?")
    assert reply.reply  # non-empty fallback
    assert reply.mode == "mock"
