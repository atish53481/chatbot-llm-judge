# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Chrome side-panel extension + local FastAPI backend that scores any chatbot's answers with DeepEval metrics using a separate judge model (OpenAI-compatible API, Groq by default). `docs/WORKFLOW.md` describes request flows end to end; `docs/superpowers/specs|plans/` hold the design docs.

## Commands (Windows, run from repo root)

```bat
run-backend.bat                                              :: creates .venv on first run, serves http://127.0.0.1:8000
.venv\Scripts\python -m pytest                               :: offline suite, no judge tokens (what CI runs)
.venv\Scripts\python -m pytest -m smoke                      :: fast wiring checks
.venv\Scripts\python -m pytest tests/test_app.py::test_name  :: single test
```

Live metrics against the real judge (spends tokens): `set RUN_LIVE_JUDGE=1`, then `.venv\Scripts\python -m pytest -m live`.

- Config comes from `.env` (see `.env.example`): `JUDGE_API_KEY` (required), `JUDGE_MODEL`, `JUDGE_BASE_URL`, `JUDGE_DB_PATH`, optional `JUDGE_EXTENSION_ID` (pins CORS to one extension id).
- `pytest.ini` disables the globally installed deepeval/opik/langsmith pytest plugins; keep that.
- No linter/formatter is configured. CI (`.github/workflows/ci.yml`) runs the offline suite on Python 3.13; the live job is manual-dispatch only.
- The extension has no build step: load `ChatbotExtension/` unpacked (see `ChatbotExtension/README.md`).

## Architecture

- **`backend/dashboard/app.py`** — the whole HTTP API. At import it opens SQLite (`storage.init_db`, which also migrates legacy target configs). CORS only allows `chrome-extension://` origins and `TrustedHostMiddleware` only `127.0.0.1`/`localhost` — deliberate (protects stored target API keys); don't loosen to `*`.
- **`backend/metrics_catalog.py`** — single source of truth for metrics. Each `MetricSpec` bundles the DeepEval metric builder, how to build an `LLMTestCase` from a golden row + reply, the dataset (`goldens` or `goldens_with_context`), and `needs` (reference data required for ad-hoc judging). 24 specs in 6 groups (`GROUPS`); each has `direction` ("higher"/"lower"), a default `threshold`, `presets()` per environment (`ENVIRONMENTS`), and `kind` ("single"/"conversation"). "lower" metrics (hallucination, bias, toxicity, pii_leakage) are reported as 1 − DeepEval score with the threshold as a maximum — DeepEval 4 scores them 1 = clean, so always go through `deepeval_threshold`/`reported_score` (the runner does). `/api/run` and `/api/judge` take the per-metric `threshold` the side panel stores in `chrome.storage.local` (`thresholds` {key: value}, `thresholdEnv`). A run passes when its average meets the threshold (`runner._meets`). Conversation scenarios: shipped in `conversations.default.json`, edited copy `conversations.json` (gitignored, reset with the goldens on every panel load, CRUD at `/api/conversations`). Security probes (8 red-team G-Eval metrics, group `security`): shipped in `security_probes.default.json`, edited copy `security_probes.json` (gitignored, reset together with the goldens, CRUD at `/api/security-probes`); a target's optional `persona` goes into each probe's context, and its `probe_set` (`ecommerce` default, or `generic`) picks which probes are sent. Every spec also carries dashboard card copy (`ui_category`, `scale_hint`, `question`), and `/api/metrics?target_id=` adds `cases_available`; specs that score `retrieval_context` use the chatbot's own (target `context_path`) and otherwise fall back to the golden's context, with each row's `context_source` = "golden" and the result's `note` saying so. Each spec's `scores_on` / `criteria` feed the dashboard; result rows carry `input`, `actual_output`, `expected_output`, `context`, `context_source`, `score`, `passed`, `reason`. Both the API and the tests import from here.
- **`backend/dashboard/runner.py`** — `run_spec` sweeps a theme's goldens against a target and stores **one** aggregated row per run (charts plot run averages); `limit` (the dashboard's cases per run) caps the cases sent and `persona` feeds the security probes; `judge_one` scores a single ad-hoc answer and persists nothing. Failures come back as `status: "error"` dicts, not exceptions.
- **`backend/targets/`** — one connector, `HttpTargetClient` (type `http`): replays a request captured from the chatbot's website. Config is `{url, method, headers, body_template, response_path, theme}`; `{{message}}` marks where the question goes (JSON-escaped in bodies, URL-encoded for form bodies/URLs). Replies come from a JSON path, from SSE `data:` events joined at that path, or as raw text when the path is empty. `curl.py` turns "Copy as cURL (bash)" into that config (`/api/targets/parse-curl`); `/api/targets/test` tries an unsaved config. Header values (cookies, keys) are masked as `***` in responses, and a PUT without `headers` keeps the stored ones. The canned-answer chatbot used by tests lives in `tests/fakes.py`, not in the product.
- **`backend/judges/judge.py`** — wraps the judge as a DeepEval `LocalModel` with rate-limit retries. The judge must never be the chatbot under test.
- **`backend/usage.py`** — in-memory judge token / call counter (`GroqJudge.load_model` wraps the OpenAI client once; `HttpTargetClient.send` counts chatbot calls), served at `/api/usage` and reset with the process or `POST /api/usage/reset`.
- **Goldens** — `backend/datasets/goldens.json`, grouped by theme (`general_support` default), edited through the API/side panel. Edits are session-only for shipped themes: the side panel calls `POST /api/goldens/reset` on every load, which restores the themes in `goldens.default.json` (the defaults to edit) and keeps other themes. The autouse fixture in `tests/conftest.py` redirects `GOLDENS_PATH` to a copy of `tests/data/goldens.json` for every non-smoke test.
- **Document → goldens (`backend/rag/`)** — `POST /api/documents/url {theme, url}` fetches a help page (`fetch.py`: http(s) only, 5 MB cap, stdlib HTML-to-text, refuses pages under 200 chars) and feeds its text through the same generator. `POST /api/documents` saves the upload under `backend/datasets/documents/<theme>/`, runs the DeepEval `Synthesizer` (judge as critic, local sentence-transformers embedder in `embeddings.py`), and appends goldens. `app.py` imports this lazily inside a wrapper named `generate_goldens_from_document` because `sentence_transformers` takes ~40s to import. Keep it out of module-level imports and keep the wrapper name: tests patch `app_module.generate_goldens_from_document` and `backend.rag.embeddings.SentenceTransformer`.
- **Storage** — `backend/storage.py`, SQLite tables for targets, runs and documents.
- **Extension** — `sidebar/` (main UI), `dashboard/` (chat, trend/latest charts), `lib/api.js` (backend client). Views share selection state through `chrome.storage.local`.

`01_Chatbot_Shopeasy_chatbot/` is a separate sample chatbot app used as a judging target, not part of the judge. It is committed without secrets: its real `.env` is gitignored and `.env.sample` holds placeholders only.
