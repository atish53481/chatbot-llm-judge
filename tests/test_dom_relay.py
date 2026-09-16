import threading
import time

import pytest

from backend.targets.dom_relay import DomRelayTargetClient, RelayQueue


def _answer_when_asked(q, session_id, answer, seen):
    """Play the content script: wait for the queued question, then relay an answer."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        question = q.next_question(session_id)
        if question is not None:
            seen.append(question)
            q.push(session_id, answer)
            return
        time.sleep(0.01)


def test_relay_queue_push_then_pop_returns_value():
    q = RelayQueue()
    q.push("session-1", "hello from DOM")
    assert q.pop("session-1", timeout=1) == "hello from DOM"


def test_relay_queue_pop_times_out_when_nothing_pushed():
    q = RelayQueue()
    assert q.pop("session-empty", timeout=0.2) is None


def test_questions_are_delivered_in_order_per_session():
    q = RelayQueue()
    q.ask("s1", "first")
    q.ask("s1", "second")
    q.ask("s2", "other")
    assert q.next_question("s1") == "first"
    assert q.next_question("s1") == "second"
    assert q.next_question("s1") is None
    assert q.next_question("s2") == "other"


def test_chat_sends_question_and_returns_relayed_answer():
    q = RelayQueue()
    client = DomRelayTargetClient(session_id="s1", queue=q, timeout=5)
    seen = []
    t = threading.Thread(target=_answer_when_asked, args=(q, "s1", "relayed answer", seen))
    t.start()
    reply = client.chat("what is the refund policy?")
    t.join()
    assert seen == ["what is the refund policy?"]
    assert reply.reply == "relayed answer"
    assert reply.mode == "dom"


def test_chat_ignores_replies_that_arrived_before_the_question():
    q = RelayQueue()
    q.push("s1", "stale text from page load")
    client = DomRelayTargetClient(session_id="s1", queue=q, timeout=5)
    seen = []
    t = threading.Thread(target=_answer_when_asked, args=(q, "s1", "fresh answer", seen))
    t.start()
    reply = client.chat("hello?")
    t.join()
    assert reply.reply == "fresh answer"


def test_chat_timeout_raises_and_withdraws_unanswered_question():
    q = RelayQueue()
    client = DomRelayTargetClient(session_id="s2", queue=q, timeout=0.2)
    with pytest.raises(TimeoutError):
        client.chat("no one will answer this")
    assert q.next_question("s2") is None


def test_health_is_ok():
    assert DomRelayTargetClient("s3", RelayQueue()).health() == {"status": "ok"}
