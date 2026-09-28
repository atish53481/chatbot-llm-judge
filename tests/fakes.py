"""Test double: a chatbot that answers from the golden set, with no network."""
from __future__ import annotations

from difflib import SequenceMatcher

from backend.datasets.goldens import load_goldens
from backend.targets.base import ChatbotClient, ChatReply

FALLBACK = "I don't have information about that. Please contact support@example.com."


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


class CannedChatbot(ChatbotClient):
    # Answers like the ShopEasy sample bot: from the shop's golden set.
    def __init__(self, theme: str = "general_support"):
        self.theme = theme

    def chat(self, message: str, history: list[dict] | None = None) -> ChatReply:
        best, best_score = None, 0.0
        for g in load_goldens(theme=self.theme):
            score = _similarity(message, g["question"])
            if score > best_score:
                best, best_score = g, score
        reply = best["expected_answer"] if best and best_score > 0.6 else FALLBACK
        return ChatReply(reply=reply, model="canned", mode="http")
