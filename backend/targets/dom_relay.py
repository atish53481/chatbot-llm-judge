"""Target for chatbots with no API.

The extension's content script types each question into the chatbot's own
page and relays the reply it sees in the DOM. The backend never touches the
page: it queues the question here and waits for the relayed answer. Every
question carries an id that the page echoes back with the reply, so a late
answer to a question that already timed out is never scored as the answer to
the next one.
"""
from __future__ import annotations

import queue as queue_module
import threading
import time
import uuid

from backend.targets.base import ChatbotClient, ChatReply

DEFAULT_TIMEOUT = 120.0  # chatbots that stream long answers need time


class RelayQueue:
    """Per-session mailboxes: questions out to the page, replies back in."""

    def __init__(self):
        self._questions: dict[str, "queue_module.Queue[tuple[str, str]]"] = {}
        self._replies: dict[str, "queue_module.Queue[tuple[str, str]]"] = {}
        self._session_locks: dict[str, threading.Lock] = {}
        self._lock = threading.Lock()

    def _get(self, boxes: dict, session_id: str, factory):
        with self._lock:
            if session_id not in boxes:
                boxes[session_id] = factory()
            return boxes[session_id]

    def _peek(self, boxes: dict, session_id: str):
        # Read paths never create mailboxes for sessions nobody asked about.
        with self._lock:
            return boxes.get(session_id)

    def session_lock(self, session_id: str) -> threading.Lock:
        """Serialises chats on one page so only one caller waits on it at a time."""
        return self._get(self._session_locks, session_id, threading.Lock)

    # Questions: backend -> page.
    def ask(self, session_id: str, question: str) -> str:
        question_id = uuid.uuid4().hex
        self._get(self._questions, session_id, queue_module.Queue).put((question_id, question))
        return question_id

    def next_question(self, session_id: str) -> dict | None:
        box = self._peek(self._questions, session_id)
        if box is None:
            return None
        try:
            question_id, question = box.get_nowait()
        except queue_module.Empty:
            return None
        return {"id": question_id, "question": question}

    def wait_question(self, session_id: str, timeout: float) -> dict | None:
        """Long-poll for the next question. Callers check the session is known."""
        box = self._get(self._questions, session_id, queue_module.Queue)
        try:
            question_id, question = box.get(timeout=timeout)
        except queue_module.Empty:
            return None
        return {"id": question_id, "question": question}

    def withdraw_questions(self, session_id: str) -> None:
        box = self._peek(self._questions, session_id)
        if box is None:
            return
        while True:
            try:
                box.get_nowait()
            except queue_module.Empty:
                return

    # Replies: page -> backend.
    def push(self, session_id: str, question_id: str, text: str) -> None:
        self._get(self._replies, session_id, queue_module.Queue).put((question_id, text))

    def pop(self, session_id: str, question_id: str, timeout: float) -> str | None:
        """Wait for the reply to one question; replies to other questions are stale and dropped."""
        box = self._get(self._replies, session_id, queue_module.Queue)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                reply_id, text = box.get(timeout=remaining)
            except queue_module.Empty:
                return None
            if reply_id == question_id:
                return text


class DomRelayTargetClient(ChatbotClient):
    def __init__(self, session_id: str, queue: RelayQueue, timeout: float = DEFAULT_TIMEOUT):
        self.session_id = session_id
        self.queue = queue
        self.timeout = timeout

    def health(self) -> dict:
        return {"status": "ok"}

    def chat(self, message: str, history: list[dict] | None = None) -> ChatReply:
        with self.queue.session_lock(self.session_id):
            question_id = self.queue.ask(self.session_id, message)
            reply_text = self.queue.pop(self.session_id, question_id, timeout=self.timeout)
            if reply_text is None:
                self.queue.withdraw_questions(self.session_id)
                raise TimeoutError(
                    f"no reply relayed for session {self.session_id!r} within "
                    f"{self.timeout}s - is the chatbot tab open with the relay armed?"
                )
        return ChatReply(reply=reply_text, model="dom-relay", mode="dom")
