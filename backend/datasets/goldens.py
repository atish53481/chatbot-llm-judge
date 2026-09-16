"""Temporary minimal version — replaced with full JSON-backed store in Task 4.
Signature (`load_goldens(theme=...)`) is kept identical so Task 4's rewrite
doesn't change any caller."""
from __future__ import annotations


def load_goldens(theme: str | None = None) -> list[dict]:
    return [
        {
            "id": "g_0001",
            "theme": "general_support",
            "question": "What is your refund window?",
            "expected_answer": (
                "Refunds are processed within 7 business days of receiving the "
                "returned item. Returns must be initiated within 30 days of delivery."
            ),
            "context": [
                "Refunds are processed within 7 business days of receiving the returned item.",
                "Items can be returned within 30 days of delivery in original condition.",
            ],
            "categories": ["policy", "refund"],
        }
    ]
