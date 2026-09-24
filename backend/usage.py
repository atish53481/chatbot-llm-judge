"""Token and call counts for the dashboard's "Tokens used" tile.

In memory, per backend process: a restart starts from zero. The judge reports
token usage on every completion; chatbots under test are arbitrary websites,
so only their calls are counted.
"""
from __future__ import annotations

import threading

_LOCK = threading.Lock()
_state = {"judge_calls": 0, "judge_prompt_tokens": 0, "judge_completion_tokens": 0, "target_calls": 0}


def _field(u, name: str) -> int:
    value = u.get(name) if isinstance(u, dict) else getattr(u, name, None)
    return int(value or 0)


def record_judge(u) -> None:
    """One judge completion; `u` is the response's usage (object, dict or None)."""
    with _LOCK:
        _state["judge_calls"] += 1
        if u is not None:
            _state["judge_prompt_tokens"] += _field(u, "prompt_tokens")
            _state["judge_completion_tokens"] += _field(u, "completion_tokens")


def record_target() -> None:
    with _LOCK:
        _state["target_calls"] += 1


def snapshot() -> dict:
    with _LOCK:
        judge_tokens = _state["judge_prompt_tokens"] + _state["judge_completion_tokens"]
        return {
            "total_tokens": judge_tokens,
            "calls": _state["judge_calls"] + _state["target_calls"],
            "target_calls": _state["target_calls"],
            "judge_calls": _state["judge_calls"],
            "judge_tokens": judge_tokens,
        }


def reset() -> None:
    with _LOCK:
        for key in _state:
            _state[key] = 0
