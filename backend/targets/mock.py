"""Canned-response target. Zero setup, same interface as a real target."""
from __future__ import annotations

from difflib import SequenceMatcher

from backend.datasets.goldens import load_goldens
from backend.targets.base import ChatbotClient, ChatReply

FALLBACK = (
    "I don't have information about that. Please contact support@example.com."
)


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


class MockTargetClient(ChatbotClient):
    def health(self) -> dict:
        return {"status": "ok"}

    def chat(self, message: str, history: list[dict] | None = None) -> ChatReply:
        goldens = load_goldens()
        best = None
        best_score = 0.0
        for g in goldens:
            score = _similarity(message, g["question"])
            if score > best_score:
                best_score = score
                best = g
        reply_text = best["expected_answer"] if best and best_score > 0.6 else FALLBACK
        return ChatReply(reply=reply_text, model="mock-canned", mode="mock")
