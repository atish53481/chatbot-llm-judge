import threading
import time

import pytest

from backend.targets.dom_relay import DomRelayTargetClient, RelayQueue


def _answer_when_asked(q, session_id, answer, seen):
    """Play the content script: take the queued question, relay an answer for its id."""
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        nxt = q.next_question(session_id)
        if nxt is not None:
            seen.append(nxt["question"])
            q.push(session_id, nxt["id"], answer)
            return
        time.sleep(0.01)


def test_pop_returns_the_reply_for_its_question():
    q = RelayQueue()
    q.push("s1", "q1", "hello from DOM")
    assert q.pop("s1", "q1", timeout=1) == "hello from DOM"


def test_pop_times_out_when_nothing_pushed():
    q = RelayQueue()
    assert q.pop("session-empty", "q1", timeout=0.2) is None


def test_pop_drops_replies_to_other_questions():
    q = RelayQueue()
    q.push("s1", "old", "stale answer")
    q.push("s1", "new", "fresh answer")
    assert q.pop("s1", "new", timeout=1) == "fresh answer"


def test_pop_times_out_when_only_stale_replies_arrive():
    q = RelayQueue()
    q.push("s1", "old", "stale answer")
    assert q.pop("s1", "new", timeout=0.2) is None


def test_questions_get_ids_and_are_delivered_in_order_per_session():
    q = RelayQueue()
    first = q.ask("s1", "first")
    second = q.ask("s1", "second")
    other = q.ask("s2", "other")
    assert len({first, second, other}) == 3
    assert q.next_question("s1") == {"id": first, "question": "first"}
    assert q.next_question("s1") == {"id": second, "question": "second"}
    assert q.next_question("s1") is None
    assert q.next_question("s2") == {"id": other, "question": "other"}


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
    q.push("s1", "earlier-question", "stale text from an earlier question")
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


def test_late_reply_to_timed_out_question_is_not_used_for_the_next_one():
    q = RelayQueue()
    client = DomRelayTargetClient(session_id="s4", queue=q, timeout=0.3)
    taken = {}

    def page_takes_question_but_never_answers():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and "first" not in taken:
            nxt = q.next_question("s4")
            if nxt is not None:
                taken["first"] = nxt
            time.sleep(0.01)

    t1 = threading.Thread(target=page_takes_question_but_never_answers)
    t1.start()
    with pytest.raises(TimeoutError):
        client.chat("first question")
    t1.join()

    client.timeout = 5
    seen = []

    def page_answers_late_then_current():
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            nxt = q.next_question("s4")
            if nxt is not None:
                q.push("s4", taken["first"]["id"], "late answer to the first question")
                seen.append(nxt["question"])
                q.push("s4", nxt["id"], "answer to the second question")
                return
            time.sleep(0.01)

    t2 = threading.Thread(target=page_answers_late_then_current)
    t2.start()
    reply = client.chat("second question")
    t2.join()
    assert seen == ["second question"]
    assert reply.reply == "answer to the second question"


def test_health_is_ok():
    assert DomRelayTargetClient("s3", RelayQueue()).health() == {"status": "ok"}
