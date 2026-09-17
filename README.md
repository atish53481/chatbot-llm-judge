# Chrome Sidebar LLM Judge

Judge any chatbot's answers with [DeepEval](https://github.com/confident-ai/deepeval)
metrics from a Chrome side panel. A local FastAPI backend scores the answers with a
separate judge model; the extension shows the scores, the trend across runs, and a
chat window for the chatbot being judged.

## Quick start (Windows)

1. Copy `.env.example` to `.env` and set `JUDGE_API_KEY` (a Groq key works with the defaults).
2. Double-click `run-backend.bat`. The first start creates `.venv` and installs the
   packages (a few minutes); later starts take seconds. The backend listens on
   `http://127.0.0.1:8000`.
3. Load the extension: see [ChatbotExtension/README.md](ChatbotExtension/README.md).

## Layout

- `backend/` — FastAPI app (`dashboard/app.py`); chatbot connectors (`targets/`: the
  sample mock, any HTTP API with an OpenAI-compatible preset, and a web-page relay for
  chatbots without an API); the judge model (`judges/judge.py`); golden answers
  (`datasets/goldens.json`); the metric catalog (`metrics_catalog.py`); run history in
  SQLite (`storage.py`, `judge.db`).
- `tests/` — pytest suite.
- `ChatbotExtension/` — Chrome extension (side panel, dashboard tab, page relay).
- `docs/superpowers/` — design spec and implementation plan.

## Configuration (.env)

| Variable | Default | Purpose |
|---|---|---|
| `JUDGE_API_KEY` | required | key for the judge model's OpenAI-compatible API |
| `JUDGE_MODEL` | `openai/gpt-oss-120b` | judge model name |
| `JUDGE_BASE_URL` | `https://api.groq.com/openai/v1` | judge API base URL |
| `JUDGE_DB_PATH` | `judge.db` | SQLite file for chatbots and run history |

The judge is never the chatbot being judged.

## Metrics

Answer relevancy, faithfulness, hallucination, bias, toxicity, PII leakage and
correctness (G-Eval). Every score is between 0 and 1, higher is better, and a metric
passes at 0.7 or above.

## Golden answers

Each chatbot is judged against one golden set (theme). `general_support` ships by
default; `legacy_store` holds the answers migrated from the earlier `evals/` suite.
Add or delete answers in the side panel. They are saved in
`backend/datasets/goldens.json`; commit that file to version your baseline.

## Tests

```bat
.venv\Scripts\python -m pytest
.venv\Scripts\python -m pytest -m smoke
```

The first command runs the offline suite (no judge tokens); the second runs only the
quick wiring checks. To run every metric against the real judge (this spends tokens):

```bat
set RUN_LIVE_JUDGE=1
.venv\Scripts\python -m pytest -m live
```
