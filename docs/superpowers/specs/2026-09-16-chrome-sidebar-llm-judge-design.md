# Chrome Sidebar LLM Judge — Design

**Date:** 2026-09-16
**Status:** Approved for planning

## Purpose

Let a user judge any production chatbot's answer quality from a Chrome
sidebar extension, using DeepEval metrics scored by a separate judge model.
The dashboard shows a live chart (per-run bar/radar + historical trend) and
lets the user chat with the target chatbot, manage golden test cases, and
run judging with zero setup via a bundled mock chatbot + default golden
theme.

Reference framework adopted almost wholesale from
[AITesterBlueprint3x/chapter_16_DeepEval_Framwork/03_DeepFramework](https://github.com/PramodDutta/AITesterBlueprint3x/tree/main/chapter_16_DeepEval_Framwork/03_DeepFramework):
target-client pattern, judge/target separation, metrics-catalog single
source of truth, FastAPI dashboard + pytest sharing the same metric specs.

## Non-goals

- Not building a hosted/multi-tenant service — single local user, backend
  runs on localhost.
- Not implementing every DeepEval metric — ship the reference's 7 chatbot
  metrics (answer relevancy, faithfulness, hallucination, bias, toxicity,
  PII leakage, GEval correctness); more can be added later without
  redesign, since `metrics_catalog.py` is additive.
- Not building real user auth — extension assumes one local user.

## Architecture

```
Chrome Extension (MV3)
├── Sidebar (chrome.sidePanel)      — target picker, golden manager, run trigger
└── Dashboard tab (chrome-extension page or opened localhost page)
        — chat window + Chart.js (bar/radar latest run, line = trend)
              │  HTTP (localhost)
              ▼
FastAPI backend (dashboard/app.py)
├── targets/            — one ChatbotClient-shaped class per way of reaching a chatbot
│   ├── http_client.py  — generic config-driven HTTP adapter (base_url, request/response field paths)
│   ├── presets.py      — named configs (OpenAI-compatible, generic REST) that pre-fill http_client
│   ├── mock.py         — canned Q&A responder, same interface, powers the zero-setup sample
│   └── dom_relay.py    — receives turns POSTed by the extension's content script
├── judges/judge.py     — separate judge model wrapper (never the target chatbot)
├── datasets/           — golden dataclasses + JSON-backed store (theme, question, expected_answer, context)
├── metrics_catalog.py  — MetricSpec registry, shared by pytest and dashboard
├── dashboard/runner.py — executes one MetricSpec against one target, returns per-case rows + aggregate
├── storage.py          — SQLite: runs(id, target_id, metric_key, score, passed, ts) for trend chart
└── tests/              — pytest suite importing the same specs (smoke tests + per-metric tests)
```

## Components

### Target connector (`targets/`)

One shape, `ChatbotClient`, with `.health()` and `.chat(message, history) -> ChatReply`.

- `HttpTargetClient(config)` — generic adapter. Config: `base_url`,
  `chat_path`, `request_template` (message field name), `response_path`
  (dotted path to reply text in JSON response), `headers`.
- Presets are just factory functions returning a pre-filled
  `HttpTargetClient` (e.g. `openai_compatible(base_url, api_key)`).
- `MockTargetClient` — no network call, returns canned replies keyed by
  fuzzy-matching input against the default golden set's questions, else a
  generic fallback string. Ships as the default target so a first run works
  immediately.
- `DomRelayTargetClient` — `.chat()` blocks on an in-memory queue populated
  by `POST /api/relay` (called by the extension's content script after it
  observes a reply in the DOM of an open chatbot tab). Used only when
  target type is `dom`.

Target configs are stored in SQLite (`targets(id, name, type, config_json)`)
so the sidebar can list/add/edit/delete them.

### Judge (`judges/judge.py`)

Ported near-verbatim from the reference: wraps DeepEval's `LocalModel`,
requires its own API key (`JUDGE_API_KEY` env var, separate from any target
credentials), rate-limit backoff, temperature 0, JSON-mode. Judge choice is
a single configured model for now (no per-target judge selection — YAGNI).

### Golden dataset (`datasets/`)

- One default theme (`general_support`) ported from the reference's
  `chatbot_goldens.py`, trimmed to ~10 cases, stored as the seed content of
  a JSON file (`datasets/goldens.json`), loaded into dataclasses at import.

  Row shape:
  ```json
  {
    "id": "g_0001",
    "theme": "general_support",
    "question": "What is your refund window?",
    "expected_answer": "Refunds are processed within 7 business days...",
    "context": ["Refunds are processed within 7 business days..."],
    "categories": ["policy", "refund"]
  }
  ```

- `POST /api/goldens {theme, question, expected_answer, context}` appends
  a row and rewrites the JSON file (single-user, no concurrent-write
  concern).
- `GET /api/goldens?theme=` / `DELETE /api/goldens/{id}` for the sidebar's
  management list.
- Existing `evals/datasets/chatbot_golden.json` content gets merged into
  `datasets/goldens.json` as part of migration (one-time script, not a
  runtime dependency).

### Metrics (`metrics_catalog.py`)

Ported as-is from the reference. `MetricSpec` dataclass: key, threshold,
`build_metric(judge)`, `build_case(golden, reply)`. Dashboard and pytest
both import `ALL_SPECS` / `SPECS_BY_KEY`.

### Judge run (`dashboard/runner.py` + `storage.py`)

`POST /api/run {target_id, metric_key}`:
1. Load target client by `target_id` (from SQLite `targets` table).
2. Load golden cases for that target's assigned theme.
3. For each case: call target, build DeepEval test case, `metric.measure()`.
4. Aggregate score/pass, insert one row per case into `storage.runs`
   (`target_id, metric_key, score, passed, ts=now`, `ts` as ISO-8601 UTC
   string, e.g. `2026-09-16T14:32:07Z`).
5. Return `{status, score, threshold, reason, rows, cases_run}` — same
   shape as the reference's `run_spec` result.

`GET /api/history?target_id=&metric_key=` — returns time-ordered rows from
`storage.runs` for the trend line chart.
`GET /api/runs/latest?target_id=` — most recent run per metric, for the
bar/radar chart.

### Chrome extension

- `manifest.json` → MV3, `side_panel: {default_path: "sidebar/sidebar.html"}`,
  permissions: `sidePanel`, `storage`, `scripting` (for DOM-relay content
  script), `host_permissions` scoped to `http://localhost:*/*` plus
  user-added target origins.
- `sidebar/` — target list (add/select/delete), golden list (add/delete),
  "Run judge" button, "Open dashboard" button.
- `dashboard/` — full page (opened as a new tab pointing at a bundled
  extension page, which iframes or fetches the localhost backend): chat
  panel (send message to selected target, shows conversation) + chart panel
  (Chart.js, bar/radar for latest run per metric, toggle to trend line).
- `content_script.js` (only injected when a DOM target is selected) —
  observes the host page's chat DOM, posts new assistant turns to
  `POST /api/relay`.
- Backend URL is fixed to `http://127.0.0.1:8000` (documented in README);
  no auto-discovery — YAGNI for a single local user.

## Data flow (happy path, sample run)

1. User installs extension, opens sidebar → default target = "Sample
   (mock)", default golden theme already seeded.
2. Clicks "Run judge" → sidebar `POST /api/run {target_id: mock, metric_key: answer_relevancy}`.
3. Backend runs mock target against goldens, judge model scores each,
   result stored in SQLite, returned to sidebar.
4. Sidebar opens/updates dashboard tab → chart panel fetches
   `/api/runs/latest` and `/api/history`, renders bar chart + trend line.
5. User later adds a real HTTP target via sidebar form, adds custom
   goldens, re-runs — same code path end to end.

## Error handling

- Target unreachable → `/api/run` returns `{status: "error", error: "..."}`;
  dashboard shows the error inline on the chart card instead of a blank
  chart (mirrors reference's `_error()` helper).
- Judge not configured (`JUDGE_API_KEY` missing) → `/api/status` reports
  `judge.up: false`; sidebar disables "Run judge" with a tooltip.
- Empty golden set for a theme → `/api/run` returns an error rather than
  running zero cases silently.
- DOM relay timeout (content script never observes a reply) → `.chat()`
  raises after N seconds, surfaced as a normal target error.

## Testing

- Keep pytest + DeepEval suite (`tests/`) as the regression gate, importing
  the same `metrics_catalog` — mirrors the reference's `tests/chatbot/`.
- `tests/test_00_smoke.py` — wiring checks (mock target reachable, judge
  configured) before any judge-token-costing test runs.
- `runner.py` / `storage.py` get plain pytest unit tests using the mock
  target (no judge/API key required) to verify aggregation and persistence
  logic independent of DeepEval's own correctness.
- Extension: manual smoke test checklist in `ChatbotExtension/README.md`
  (load unpacked, open sidebar, run sample, confirm chart renders) — no
  automated browser tests in this iteration (YAGNI given single developer,
  can add Playwright later if it grows).

## Migration from current scaffold

- `backend.py` (Flask, pytest-output-parsing) is replaced by the FastAPI
  `dashboard/app.py` design above; keep `backend.py` only if the user wants
  the old raw-pytest-runner UI too, otherwise delete once FastAPI backend
  is verified equivalent-or-better.
- `evals/` pytest files get refactored to import from `metrics_catalog.py`
  instead of ad hoc metric construction, feeding off the same golden JSON.
- `ChatbotExtension/` (popup-based) becomes the base for the new
  sidebar-based extension; `popup/` files are replaced by `sidebar/`.
