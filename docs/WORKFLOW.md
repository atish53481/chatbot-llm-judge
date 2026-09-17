# Application workflow

How a judged answer travels from a click in the side panel to a score on the chart.

## Components

| Piece | Path | Role |
|---|---|---|
| Side panel | `ChatbotExtension/sidebar/` | Primary UI: manage chatbots and goldens, run a metric, see the latest scores. |
| Dashboard tab | `ChatbotExtension/dashboard/` | Chat with the chatbot; latest / trend / case-by-case views. |
| Service worker | `ChatbotExtension/background.js` | Opens the views; forwards relay traffic (a page cannot fetch `127.0.0.1`). |
| Content script | `ChatbotExtension/content_script.js` | Drives a chatbot web page: types questions, reads replies. |
| Backend | `backend/dashboard/app.py` | FastAPI control plane on `http://127.0.0.1:8000`. |
| Judge | `backend/judges/judge.py` | The scoring model — never the chatbot under test. |
| Metrics catalog | `backend/metrics_catalog.py` | The 7 metrics, their cases and the 0.7 threshold. |
| Storage | `backend/storage.py` | SQLite (`judge.db`): targets, run history, seed flag. |
| Goldens | `backend/datasets/goldens.json` | Question / expected-answer sets by theme. |

```
 side panel ──┐
              ├── fetch ──► FastAPI (127.0.0.1:8000) ──► judge model (remote API)
 dashboard ───┘                    │
                                   └──► SQLite judge.db (targets, runs)
 chatbot web page ── content script ── service worker ──► relay endpoints
```

## 1. Startup

1. `run-backend.bat` creates `.venv` on first use, installs `requirements.txt`, then
   starts uvicorn. `.env` supplies `JUDGE_API_KEY` (and optional `JUDGE_MODEL`,
   `JUDGE_BASE_URL`, `JUDGE_DB_PATH`).
2. On import, `backend/dashboard/app.py` opens `judge.db`, creates the tables, and
   seeds the *Sample chatbot* once (`storage.seed_sample_target_once`).
3. The extension loads from `ChatbotExtension/`. The side panel calls `/api/status`,
   which reports the judge model name and whether a key is configured.

## 2. Adding a chatbot (target)

`POST /api/targets` with `{name, type, config, preset?}`. `type` is one of:

- **`mock`** — canned answers for the sample; needs no setup.
- **`http` + `commandcode` preset** — a real model served by Command Code's Provider
  API (`https://api.commandcode.ai/provider/v1`). Only `api_key` is required; the
  preset fills the endpoint, request shape and default model
  (`z-ai/glm-5.3-flash`). The endpoint serves open models (glm, qwen, deepseek,
  minimax); Anthropic ids live on `/v1/messages` and `openai/*` ids are rejected.
- **`http` + `openai_compatible` preset** — calls any OpenAI-chat-completions API
  from `{base_url, api_key, model}`.
- **`http`** — a hand-configured API: chat path, message field, and the dotted path
  to the reply (`backend/targets/http_client.py`).
- **`dom`** — a chatbot web page with no API. The side panel requests site access,
  injects `content_script.js` into the active tab, and stores
  `config.session_id = <hostname>`.

The backend builds the client once at create time, so a broken config is rejected
with a 400 before it is stored. Responses mask secret header values as `***`.

## 3. Running one metric

`POST /api/run {target_id, metric_key}` → `backend/dashboard/runner.py:run_spec`:

1. `spec.cases(theme)` loads the chatbot's golden set from `goldens.json`
   (`goldens_with_context` keeps only rows that carry context).
2. The judge is built from the environment (`build_judge`); without a key the route
   returns 503.
3. For each golden: `target.chat(question)` gets the chatbot's reply, the metric
   measures `spec.build_case(golden, reply)`, and the case's score, pass flag and
   reason are collected.
4. The run's **average** score and overall pass flag are written as **one** row in
   `runs` (`storage.record_run`) — the charts plot run averages, not cases.
5. The response carries `status`, `score`, `threshold`, `reason`, and per-case
   `rows` for the case table.

Failures are returned as `status: "error"` with a message (broken dataset, empty
dataset, target/judge exception) rather than raising.

## 4. Web-page relay (`dom` targets)

For chatbots that only exist in a browser page, the side panel cannot talk to them
directly — an `https` page cannot fetch `http://127.0.0.1`. So:

1. The user clicks the page's message box, then its Send button (or Esc so the
   script synthesizes Enter), then the area where replies appear.
2. `content_script.js` polls `/api/relay/next` (long-poll) through the service
   worker every second.
3. For each question it sets the text (React-safe native setter, or
   `execCommand("insertText")`), submits, and waits for the reply area to settle
   (~1.5 s quiet) before reading the appended text.
4. The reply is sent to `POST /api/relay`; `runner.run_spec` receives it as the
   chatbot's answer via `DomRelayTargetClient`.

`/api/relay*` rejects unknown sessions (404); sessions are matched against stored
`dom` targets by hostname.

## 5. Chat (dashboard)

`POST /api/chat {target_id, message, history}` calls `target.chat` and returns the
reply plus the model and mode. Any chatbot failure becomes a 502 with the error
type. The dashboard keeps the conversation per target in memory and sends the last
40 turns, so a real chatbot sees its own earlier replies; switching targets starts a
fresh conversation. This is a convenience view; the judged answer is the one the
metric run collects.

### Judging one answer on the spot

Every chatbot reply carries a **Judge this answer** button. It scores that single
answer with the selected metric(s) through `POST /api/judge`:

```
{metric_key, question, actual_output, theme?, expected_answer?, context?}
```

`judge_one` (`backend/dashboard/runner.py`) builds the same `LLMTestCase` the sweep
would, from the supplied question and answer — no dataset and no target call. Metrics
that cannot score without reference data declare it in `MetricSpec.needs`
(`correctness` → `expected_answer`; `faithfulness`, `hallucination` → `context`). When
the question is one of the theme's goldens, the route fills those in from the golden
row; otherwise the call returns `status: "error"` saying what is missing rather than
scoring against an empty reference.

Ad-hoc scores are **not stored**, so the trend charts stay a record of golden-set runs.

## 6. Views and history

- The side panel and dashboard sync selections through `chrome.storage.local`
  (`selectedTargetId`, `selectedMetric`, `lastRun`); the latest run is shared that
  way rather than re-fetched.
- `GET /api/runs/latest?target_id=` returns the newest run per metric
  (`storage.latest_runs`) for the latest-scores chart.
- `GET /api/history?target_id=&metric_key=` returns every run of one metric in
  order (`storage.history`) for the trend chart.
- Charts re-render on OS dark-mode changes; each dashboard chart has a table
  fallback.

## 7. Tests and CI

- `python -m pytest` — offline suite, no judge tokens.
- `python -m pytest -m smoke` — fast wiring checks only.
- `set RUN_LIVE_JUDGE=1` then `python -m pytest -m live` — every metric against the
  real judge (spends tokens).
- CI (`.github/workflows/ci.yml`) runs the offline suite on pushes to `main` and on
  pull requests; the live suite is a manual `workflow_dispatch` job that needs the
  `JUDGE_API_KEY` repository secret.

## Where to change behaviour

| Want to change | Edit |
|---|---|
| Add or retune a metric | `backend/metrics_catalog.py` (one `MetricSpec`, then `ALL_SPECS`) |
| What a metric needs to score | `MetricSpec.needs` in `backend/metrics_catalog.py` |
| Single-answer judging | `judge_one` in `backend/dashboard/runner.py`, `POST /api/judge` |
| Pass threshold | `PASS_THRESHOLD` in `backend/metrics_catalog.py` |
| Judge model / provider | `.env`: `JUDGE_MODEL`, `JUDGE_BASE_URL`, `JUDGE_API_KEY` |
| Golden answers | side panel (`POST` / `PUT` / `DELETE` on `/api/goldens`), or `backend/datasets/goldens.json` |
| HTTP request shape | `backend/targets/http_client.py`, `backend/targets/presets.py` |
| Relay timing | `POLL_MS` / `SETTLE_MS` / `REPLY_TIMEOUT_MS` in `ChatbotExtension/content_script.js`; `DEFAULT_TIMEOUT` in `backend/targets/dom_relay.py`; `RELAY_WAIT_SECONDS` in `backend/dashboard/app.py` |
| API surface | `backend/dashboard/app.py` |
