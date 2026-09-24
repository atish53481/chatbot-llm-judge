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

from backend import usage

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

    def load_model(self, async_mode: bool = False):
        # LocalModel.generate reads only the reply text, so the usage the
        # response carries is picked up here, on the client it calls.
        client = super().load_model(async_mode=async_mode)
        if async_mode:
            return client  # a_generate goes through the sync path above
        create = client.chat.completions.create
        if getattr(create, "counts_usage", False):
            return client  # already wrapped: DeepEval loads the model at init and per call

        def counted(*args, **kwargs):
            response = create(*args, **kwargs)
            usage.record_judge(getattr(response, "usage", None))
            return response

        counted.counts_usage = True
        client.chat.completions.create = counted
        return client


def judge_config(
    api_key: str | None = None, model: str | None = None, base_url: str | None = None
) -> dict:
    """The judge's settings: values passed in (saved from the side panel) win,
    then the environment / .env, then the defaults."""
    return {
        "api_key": api_key or os.getenv(JUDGE_API_KEY_ENV, ""),
        "model": model or os.getenv(JUDGE_MODEL_ENV, DEFAULT_MODEL),
        "base_url": base_url or os.getenv(JUDGE_BASE_URL_ENV, DEFAULT_BASE_URL),
    }


def build_judge(
    api_key: str | None = None, model: str | None = None, base_url: str | None = None
) -> GroqJudge:
    config = judge_config(api_key, model, base_url)
    if not config["api_key"]:
        raise RuntimeError(
            f"{JUDGE_API_KEY_ENV} is not set. Add the judge's API key in the side panel "
            "(Judge settings) or in the .env file."
        )
    return GroqJudge(
        model=config["model"],
        api_key=config["api_key"],
        base_url=config["base_url"],
        temperature=0.0,
        format="json",
    )


def judge_name(model: str | None = None) -> str:
    return judge_config(model=model)["model"]
