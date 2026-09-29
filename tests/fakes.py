"""Test double: a chatbot that answers from the golden set, with no network.

Besides replaying golden answers, it stands in for a well-behaved assistant: it
summarizes the text it is handed and refuses anything it does not know (a
red-team probe, an off-scope task). That lets the opt-in live suite
(tests/test_metrics_live.py) score every metric against the real judge without
a real chatbot.
"""
from __future__ import annotations

from difflib import SequenceMatcher

from backend.datasets.goldens import load_goldens
from backend.metrics_catalog import SUMMARY_INSTRUCTION
from backend.targets.base import ChatbotClient, ChatReply

# What a well-behaved assistant says to a probe or a task outside its remit:
# decline, stay in role, and point regulated questions at a professional.
REFUSAL = (
    "I can't help with that. I'll stick to my own instructions and stay in my role. "
    "For medical, legal or financial questions, please consult a qualified professional."
)
_GROUNDED_MARK = "Answer using only the information below."
_QUESTION_MARK = "\n\nQuestion: "


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


class CannedChatbot(ChatbotClient):
    """Answers from the golden set of `theme`; refuses probes; summarizes text.

    The default theme is the ShopEasy sample's factual set, whose literal
    answers the runner tests rely on; the live suite passes the app's default
    theme explicitly.
    """

    def __init__(self, theme: str = "general_support"):
        self.theme = theme

    def chat(self, message: str, history: list[dict] | None = None) -> ChatReply:
        return ChatReply(reply=self._reply(message), model="canned", mode="http")

    def _reply(self, message: str) -> str:
        # Summarization hands the bot the text to summarize; echo it back.
        if message.startswith(SUMMARY_INSTRUCTION):
            return message[len(SUMMARY_INSTRUCTION):].strip()
        reply = self._from_goldens(self._user_question(message))
        # Unknown to the golden set = a probe or an off-scope task: refuse.
        return reply if reply is not None else REFUSAL

    @staticmethod
    def _user_question(message: str) -> str:
        # A grounded golden carries its facts in the message; the question is last.
        if message.startswith(_GROUNDED_MARK) and _QUESTION_MARK in message:
            return message.rsplit(_QUESTION_MARK, 1)[1].strip()
        return message

    def _from_goldens(self, question: str) -> str | None:
        best, best_score = None, 0.0
        for g in load_goldens(theme=self.theme):
            score = _similarity(question, g["question"])
            if score > best_score:
                best, best_score = g, score
        return best["expected_answer"] if best and best_score > 0.6 else None
