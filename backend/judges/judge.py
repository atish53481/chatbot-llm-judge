"""The judge model: scores every DeepEval metric. Never the target chatbot.

Ported from the AITesterBlueprint3x reference framework's llm_providers/judge.py.
"""
from __future__ import annotations

import asyncio
import os
import random
import threading
import time

from deepeval.models import LocalModel
from dotenv import load_dotenv

load_dotenv()

JUDGE_MODEL_ENV = "JUDGE_MODEL"
JUDGE_BASE_URL_ENV = "JUDGE_BASE_URL"
JUDGE_API_KEY_ENV = "JUDGE_API_KEY"

DEFAULT_MODEL = "openai/gpt-oss-120b"
DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"

_LOCK = threading.Lock()
_MAX_RETRIES = 5


def _is_rate_limit(err: Exception) -> bool:
    text = str(err).lower()
    needles = ("rate_limit", "ratelimit", "429", "too many requests",
               "retryerror", "tokens per minute", "otpm")
    return any(n in text for n in needles)


class GroqJudge(LocalModel):
    """LocalModel plus serialisation and backoff for rate limits."""

    def _call(self, fn, *args, **kwargs):
        for attempt in range(_MAX_RETRIES):
            try:
                with _LOCK:
                    return fn(*args, **kwargs)
            except Exception as e:  # noqa: BLE001 - provider raises many types
                if not _is_rate_limit(e) or attempt == _MAX_RETRIES - 1:
                    raise
                delay = min(70, 25 * (attempt + 1)) + random.uniform(0, 5)
                time.sleep(delay)
        raise RuntimeError("unreachable")

    def generate(self, *args, **kwargs):
        return self._call(super().generate, *args, **kwargs)

    async def a_generate(self, *args, **kwargs):
        # Same lock and rate-limit backoff as the sync path.
        return await asyncio.to_thread(self.generate, *args, **kwargs)


def build_judge() -> GroqJudge:
    api_key = os.getenv(JUDGE_API_KEY_ENV, "")
    if not api_key:
        raise RuntimeError(
            f"{JUDGE_API_KEY_ENV} is not set. Add it to your environment or .env file."
        )
    return GroqJudge(
        model=os.getenv(JUDGE_MODEL_ENV, DEFAULT_MODEL),
        api_key=api_key,
        base_url=os.getenv(JUDGE_BASE_URL_ENV, DEFAULT_BASE_URL),
        temperature=0.0,
        format="json",
    )


def judge_name() -> str:
    return os.getenv(JUDGE_MODEL_ENV, DEFAULT_MODEL)
