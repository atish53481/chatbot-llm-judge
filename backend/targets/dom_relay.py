"""Target for chatbots with no API.

The extension's content script types each question into the chatbot's own
page and relays the reply it sees in the DOM. The backend never touches the
page: it queues the question here and waits for the relayed answer.
"""
from __future__ import annotations

import queue as queue_module
import threading

from backend.targets.base import ChatbotClient, ChatReply

DEFAULT_TIMEOUT = 60.0


def _drain(box: "queue_module.Queue[str]") -> None:
    while True:
        try:
            box.get_nowait()
        except queue_module.Empty:
            return


class RelayQueue:
    """Per-session mailboxes: questions out to the page, replies back in."""

    def __init__(self):
        self._questions: dict[str, "queue_module.Queue[str]"] = {}
        self._replies: dict[str, "queue_module.Queue[str]"] = {}
        self._session_locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    def _get(self, boxes: dict, session_id: str, factory):
        with self._lock:
            if session_id not in boxes:
                boxes[session_id] = factory()
            return boxes[session_id]

    def session_lock(self, session_id: str) -> threading.Lock:
        """Serialises chats on one page so questions and answers cannot cross."""
        return self._get(self._session_locks, session_id, threading.Lock)

    # Questions: backend -> page.
    def ask(self, session_id: str, question: str) -> None:
        self._get(self._questions, session_id, queue_module.Queue).put(question)

    def next_question(self, session_id: str) -> str | None:
        try:
            return self._get(self._questions, session_id, queue_module.Queue).get_nowait()
        except queue_module.Empty:
            return None

    def withdraw_questions(self, session_id: str) -> None:
        _drain(self._get(self._questions, session_id, queue_module.Queue))

    # Replies: page -> backend.
    def push(self, session_id: str, text: str) -> None:
        self._get(self._replies, session_id, queue_module.Queue).put(text)

    def pop(self, session_id: str, timeout: float) -> str | None:
        try:
            return self._get(self._replies, session_id, queue_module.Queue).get(timeout=timeout)
        except queue_module.Empty:
            return None

    def drain_replies(self, session_id: str) -> None:
        _drain(self._get(self._replies, session_id, queue_module.Queue))


class DomRelayTargetClient(ChatbotClient):
    def __init__(self, session_id: str, queue: RelayQueue, timeout: float = DEFAULT_TIMEOUT):
        self.session_id = session_id
        self.queue = queue
        self.timeout = timeout

    def health(self) -> dict:
        return {"status": "ok"}

    def chat(self, message: str, history: list[dict] | None = None) -> ChatReply:
        with self.queue.session_lock(self.session_id):
            # A reply that arrived before this question belongs to nobody;
            # drop it so it cannot be scored as this question's answer.
            self.queue.drain_replies(self.session_id)
            self.queue.ask(self.session_id, message)
            reply_text = self.queue.pop(self.session_id, timeout=self.timeout)
            if reply_text is None:
                self.queue.withdraw_questions(self.session_id)
                raise TimeoutError(
                    f"no reply relayed for session {self.session_id!r} within "
                    f"{self.timeout}s - is the chatbot tab open with the relay armed?"
                )
        return ChatReply(reply=reply_text, model="dom-relay", mode="dom")
