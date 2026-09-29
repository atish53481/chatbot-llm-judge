# Background runs — design

Date: 2026-09-29
Status: approved in chat, awaiting spec review

## Goal

A run keeps going when the side panel or dashboard that started it is closed,
and the user still hears when it finishes.

Today `runMetrics()` in `lib/ui.js` loops over the metrics in the page, calling
`POST /api/run` once per metric and polling `/api/run/progress`. Closing the
page ends the loop: the metric in flight finishes on the backend, the rest never
start, and no notification is sent. Case details live only in that browser's
`chrome.storage.local`, so the PDF report cannot show cases for runs made elsewhere.

Success:

- Start a multi-metric run, close the side panel: every metric still runs and is
  recorded, and a notification arrives when the batch ends.
- Reopen the side panel or dashboard mid-run: it shows the live progress and Stop works.
- Details and the PDF report show a run's cases from the backend, whichever
  browser page started it.
- Offline pytest stays green; `POST /api/run` keeps working for existing callers.

## Non-goals

- No parallel runs: jobs run one at a time (token counts per run rely on it).
- No scheduled or recurring runs.
- Nothing runs while the backend is down (the always-on task covers that).
- No change to how a single metric is scored (`runner.run_spec` is reused as is).

## Backend

### Jobs (`backend/dashboard/jobs.py`)

A job is one batch: a target and an ordered list of metric keys, with per-metric
thresholds, cases per run (`limit`) and `check_consistency`.

- `JobQueue` owns one worker thread started with the app. It takes queued jobs in
  creation order and runs their metrics one after another through `run_spec`,
  exactly as `/api/run` does today (same judge, same target client, same persona,
  probe set and theme from the target's config).
- Progress: the job keeps `current` = `{metric_key, index, count, done, total,
  phase, question}`, updated from `run_spec`'s `on_progress`.
- Cancel: `cancel(job_id)` sets a flag; `on_progress` raises `RunCancelled` at the
  next step, so the judge call in flight finishes first (today's Stop behaviour).
  A queued job that is cancelled never starts. The metric that was stopped comes
  back `cancelled` and is not recorded; remaining metrics are skipped.
- A metric that errors (service message, HTTP error) is recorded in the job's
  results as an error and the job continues with the next metric, as today.
- The judge is built when a job starts; a missing judge key fails the job with the
  same message `/api/run` gives (503 today).

### Storage (`backend/storage.py`)

- New table `jobs(id, target_id, status, metric_keys, options, results, current,
  error, created_at, started_at, finished_at)`; JSON columns as text, times
  ISO-8601 UTC. `status`: `queued`, `running`, `done`, `cancelled`, `error`,
  `interrupted`.
- On start, jobs left `queued` or `running` by a stopped backend become
  `interrupted` with the error "the backend stopped before this run finished; run
  it again" (as document jobs do today).
- `runs.result_json` (new column): the full `run_spec` result (rows, note, judge
  facts) for each recorded run, so Details and the report can read cases from the
  backend. Older runs have it null.

### API (`backend/dashboard/app.py`)

- `POST /api/jobs` `{target_id, metric_keys, thresholds?: {key: value}, limit?,
  check_consistency?}` → `{job_id, status}`. 404 for an unknown target or metric,
  503 when the judge is not configured (checked up front).
- `GET /api/jobs/{id}` → `{id, target_id, status, metric_keys, results: [result
  per finished metric], current, error, created_at, finished_at}`.
- `GET /api/jobs?target_id=&active=1` → jobs still `queued` or `running` for that
  target (newest first), so a reopened page can reattach.
- `POST /api/jobs/{id}/cancel` → `{cancelling: bool}`.
- `GET /api/runs/latest` rows gain `result` (parsed `result_json`, or null).
- `POST /api/run` and `/api/run/progress` stay for compatibility.

## Extension

- `runMetrics(target, keys, onProgress, control)` keeps its signature and callback
  shape: it creates a job, then polls `GET /api/jobs/{id}` every second, calling
  `onProgress(key, null, progress)` for the running metric and
  `onProgress(key, result)` once per finished metric. `stopRun(control)` cancels
  the job. The side panel and dashboard need no changes to use it.
- On load (and on target change), both views ask `GET /api/jobs?active=1` for the
  selected target and, if a job is running, reattach: live progress, the Stop
  button, and results as they arrive.
- Case details: Details and the report read `result` from `/api/runs/latest`
  first and fall back to `chrome.storage.local` for runs saved before this change.
- Notifications: when a page starts a job it sends `WATCH_JOB {job_id, target}` to
  the service worker. The worker stores watched jobs in `chrome.storage.session`,
  checks them with a `chrome.alarms` alarm every 30 seconds, and when a job ends
  sends the notification and badge (the existing `runAnnouncement`, moved where
  the worker can load it). A page that sees its job end sends `JOB_ENDED {job_id}`
  so the worker checks at once (no 30-second wait while a page is open). The
  worker is the only sender and remembers announced job ids, so a job is
  announced exactly once. New permission: `alarms`.

## Error handling

- Backend unreachable while polling: the page keeps polling and shows "Backend not
  reachable, retrying…"; the job itself is unaffected.
- A job whose target was deleted fails with "chatbot deleted".
- Cancelled or interrupted jobs are not announced as finished; an interrupted job
  is announced once as "run interrupted" so the user knows to rerun it.

## Testing

- `tests/test_jobs.py`: queue order and one-at-a-time execution; results per
  metric; an erroring metric does not stop the job; cancel while running and
  while queued; interrupted jobs on start; `result_json` saved per run.
- `tests/test_app.py`: the four job endpoints (create, get, list active, cancel),
  404/503 cases, `result` in `/api/runs/latest`.
- Existing `/api/run` and runner tests unchanged and green.
- Extension: `runAnnouncement` checks kept; the job polling adapter is checked in
  Node with a stubbed `api`.
