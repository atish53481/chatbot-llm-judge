"""Common shape every target connector implements."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ChatReply:
    reply: str
    model: str
    mode: str  # "mock" | "http" | "dom"


class ChatbotClient(ABC):
    @abstractmethod
    def health(self) -> dict:
        ...

    @abstractmethod
    def chat(self, message: str, history: list[dict] | None = None) -> ChatReply:
        ...
