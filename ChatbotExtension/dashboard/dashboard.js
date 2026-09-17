// Dashboard tab: chat with the selected chatbot, run the judge, and read the
// latest scores, the trend across runs, and the last run case by case.

const $ = (id) => document.getElementById(id);

// Short card copy per metric. Purely presentational — the backend doesn't
// send descriptions, so this stays in sync by hand with metrics_catalog.py.
const METRIC_DESCRIPTIONS = {
  answer_relevancy: "Does the reply address the question that was actually asked?",
  faithfulness: "Is every claim in the reply grounded in the retrieved context?",
  hallucination: "Does the reply contradict or invent facts against ground truth?",
  bias: "Does the bot stay neutral when baited with a prejudiced prompt?",
  toxicity: "Is the reply free of insults, mockery and demeaning language?",
  pii_leakage: "Does the reply leak personal data or the hidden system prompt?",
  correctness: "Does the reply match the reference answer on the facts that matter?",
};

const CATEGORY_LABELS = { quality: "Quality", safety: "Safety", geval: "G-Eval" };

const state = {
  targets: [],
  metrics: [],
  judgeUp: false,
  running: false,
  trendView: "chart",
  trendChart: null,
  history: [],
  view: "judge",
  categoryFilter: "all",
  goldenCount: null,
  runningKeys: new Set(),
  expandedDetailKeys: new Set(),
  caseDetailsCache: {},
};

function currentTarget() {
  const id = Number($("target-select").value);
  return state.targets.find((t) => t.id === id) || null;
}

function setStatus(text, isError = false) {
  $("status").textContent = text || "";
  $("status").className = isError ? "status-error" : "secondary";
}

function updateRunButton() {
  const button = $("run-button");
  button.disabled = !state.judgeUp || !currentTarget() || state.running;
  button.title = state.judgeUp ? "" : "Judge not configured: JUDGE_API_KEY is missing";
}

async function refreshJudge() {
  try {
    const status = await api("/api/status");
    state.judgeUp = status.judge.up;
    if (!state.judgeUp) {
      setStatus("Judge not configured: add JUDGE_API_KEY to .env, then restart the backend.", true);
    }
  } catch (error) {
    state.judgeUp = false;
    setStatus(error.message, true);
  }
  updateRunButton();
}

function appendBubble(kind, text, meta, judgeTarget) {
  const log = $("chat-log");
  let bubble;
  log.append(
    (bubble = el(
      "div",
      { className: `bubble ${kind}` },
      text,
      meta ? el("span", { className: "meta" }, meta) : null,
      judgeTarget
        ? el(
            "button",
            {
              type: "button",
              className: "judge-answer",
              onclick: (event) => {
                event.target.remove();
                judgeAnswer(judgeTarget.question, judgeTarget.actual_output, bubble);
              },
            },
            "Judge this answer",
          )
        : null,
    )),
  );
  log.scrollTop = log.scrollHeight;
  return bubble;
}

// One badge per metric in a judge result card: icon + score, reason behind a toggle.
function judgeBadge(title, result, error) {
  if (error) {
    return el("li", { className: "judge-badge status-error" }, `! ${title}: ${error}`);
  }
  if (result.status === "error") {
    return el("li", { className: "judge-badge status-error" }, `! ${title}: ${result.error}`);
  }
  const icon = result.status === "pass" ? "✓" : "✕";
  const summary = el(
    "button",
    { type: "button", className: `judge-badge-toggle status-${result.status}` },
    `${icon} ${title} ${formatScore(result.score)} / ${formatScore(result.threshold)}`,
  );
  const reasonEl = el("p", { className: "judge-badge-reason hidden" }, result.reason || "No reason given.");
  summary.addEventListener("click", () => reasonEl.classList.toggle("hidden"));
  return el("li", { className: "judge-badge" }, summary, reasonEl);
}

// Ad-hoc judging: score the answer on screen, using the selected metric(s).
// Nothing is recorded, so the trend charts stay a record of golden-set runs.
// Results render as one card attached under the judged bubble, not separate
// chat bubbles, so the log doesn't fill up with score noise.
async function judgeAnswer(question, actualOutput, bubble) {
  const target = currentTarget();
  if (!target) return;
  const choice = $("metric-select").value;
  const keys = choice === ALL_METRICS ? state.metrics.map((m) => m.key) : [choice];
  const titles = new Map(state.metrics.map((m) => [m.key, m.title]));
  const list = el("ul", { className: "judge-badges" });
  bubble.append(list);
  setStatus(`Judging this answer with ${keys.length} metric${keys.length > 1 ? "s" : ""}…`);
  for (const key of keys) {
    const title = titles.get(key) || key;
    let result;
    let error;
    try {
      result = await api("/api/judge", {
        method: "POST",
        body: {
          metric_key: key,
          question,
          actual_output: actualOutput,
          theme: themeOf(target),
        },
      });
    } catch (e) {
      error = e.message;
    }
    list.append(judgeBadge(title, result, error));
  }
  $("chat-log").scrollTop = $("chat-log").scrollHeight;
  setStatus("Judged that answer. These scores are not saved to the trend.");
}

async function loadTargets(preferredId) {
  state.targets = await fetchTargets();
  const wanted = preferredId ?? (await settings.get("selectedTargetId", null));
  const selected = state.targets.some((t) => t.id === wanted) ? wanted : state.targets[0].id;
  fillSelect(
    $("target-select"),
    state.targets.map((t) => ({ value: t.id, label: targetLabel(t) })),
    selected,
  );
  await onTargetChanged();
}

async function onTargetChanged() {
  const target = currentTarget();
  await settings.set("selectedTargetId", target ? target.id : null);
  $("chat-log").replaceChildren();
  // A real chatbot keeps its context per conversation; a new target starts fresh.
  state.history = [];
  if (target) appendBubble("note", `Chatting with ${target.name}. Golden set: ${themeOf(target)}.`);
  // Cached case details belong to the target that was selected before.
  state.expandedDetailKeys.clear();
  state.caseDetailsCache = {};
  updateRunButton();
  renderCases(await settings.get("lastRun", null));
  await Promise.all([renderLatest(), renderTrend(), renderGoldenCount()]);
  if (state.view === "history") await renderHistory();
}

async function renderGoldenCount() {
  const target = currentTarget();
  if (!target) return;
  try {
    const goldens = await api(`/api/goldens?theme=${encodeURIComponent(themeOf(target))}`);
    state.goldenCount = goldens.length;
  } catch {
    state.goldenCount = null;
  }
  $("status-goldens-text").textContent =
    state.goldenCount === null ? "–" : `${state.goldenCount} golden answer${state.goldenCount === 1 ? "" : "s"}`;
}

// Top status strip: chatbot + judge health (from /api/status, refreshJudge)
// plus a pass/fail/not-run tally across every metric's latest run.
function renderStatusStrip(rows) {
  const target = currentTarget();
  $("status-chatbot-dot").className = `dot ${target ? "dot-good" : "dot-off"}`;
  $("status-chatbot-text").textContent = target ? `Chatbot · ${target.name}` : "Chatbot · none selected";
  $("status-judge-dot").className = `dot ${state.judgeUp ? "dot-good" : "dot-off"}`;
  $("status-judge-text").textContent = state.judgeUp ? "Judge ready" : "Judge not configured";
  const byKey = new Map(rows.map((r) => [r.metric_key, r]));
  const total = state.metrics.length;
  const pass = state.metrics.filter((m) => byKey.get(m.key)?.passed === true).length;
  const fail = state.metrics.filter((m) => byKey.get(m.key) && !byKey.get(m.key).passed).length;
  const pending = total - pass - fail;
  $("status-pass").textContent = `${pass} pass`;
  $("status-fail").textContent = `${fail} fail`;
  $("status-pending").textContent = `${pending} not run`;
}

// One row per golden case: the question, the chatbot's actual answer, and
// the judge's score/reason for it — the "what was it scored on" the Details
// toggle exists to answer.
function caseDetailPanel(metric, details) {
  if (!details) {
    return el("p", { className: "case-detail-empty muted" }, "Run this metric to see the question and answer behind each score.");
  }
  if (details.status === "error") {
    return el("p", { className: "case-detail-empty status-error" }, details.error || "This run failed.");
  }
  if (!details.rows.length) {
    return el("p", { className: "case-detail-empty muted" }, "No cases in the last run.");
  }
  return el(
    "div",
    { className: "case-detail" },
    el("p", { className: "muted small" }, `As of ${formatRunTime(details.finishedAt)}`),
    el(
      "ul",
      { className: "case-list" },
      ...details.rows.map((row) => {
        const reasonEl = el("p", { className: "case-reason hidden" }, row.reason || "No reason given.");
        const toggle = el(
          "button",
          { type: "button", className: `case-score status-${row.passed ? "pass" : "fail"}` },
          `${row.passed ? "✓" : "✕"} ${formatScore(row.score)} — why?`,
        );
        toggle.addEventListener("click", () => reasonEl.classList.toggle("hidden"));
        return el(
          "li",
          { className: "case-row" },
          el("p", { className: "case-q" }, `Q: ${row.question}`),
          el("p", { className: "case-a" }, `A: ${row.actual_output}`),
          toggle,
          reasonEl,
        );
      }),
    ),
  );
}

// Keeps an already-open detail panel in sync with a metric that just
// finished running, instead of showing scores from before the run.
function refreshExpandedDetail(key, result) {
  if (!state.expandedDetailKeys.has(key)) return;
  state.caseDetailsCache[key] = {
    finishedAt: Date.now(),
    status: result.status,
    error: result.error || null,
    rows: result.rows || [],
  };
}

async function toggleCardDetails(key) {
  if (state.expandedDetailKeys.has(key)) {
    state.expandedDetailKeys.delete(key);
    renderMetricGrid();
    return;
  }
  state.expandedDetailKeys.add(key);
  const target = currentTarget();
  if (target) state.caseDetailsCache[key] = await loadCaseDetails(target.id, key);
  renderMetricGrid();
}

function metricCard(metric, row) {
  const isRunning = state.runningKeys.has(metric.key);
  const hasRun = Boolean(row);
  const statusClass = isRunning ? "running" : !hasRun ? "not-run" : row.passed ? "pass" : "fail";
  const statusLabel = isRunning ? "RUNNING…" : !hasRun ? "NOT RUN" : row.passed ? "PASS" : "FAIL";
  const goldenText =
    state.goldenCount === null
      ? ""
      : `${state.goldenCount} golden answer${state.goldenCount === 1 ? "" : "s"}`;
  return el(
    "div",
    { className: `metric-card status-${statusClass}` },
    el(
      "div",
      { className: "metric-card-head" },
      el("span", { className: `chip category-${metric.category}` }, CATEGORY_LABELS[metric.category] || metric.category),
      el("span", { className: `metric-card-status status-${statusClass}` }, statusLabel),
    ),
    el("h3", {}, metric.title),
    el("p", { className: "metric-card-desc" }, METRIC_DESCRIPTIONS[metric.key] || ""),
    isRunning
      ? el("div", { className: "run-bar running" }, el("span", { className: "run-bar-fill" }))
      : el(
          "div",
          { className: "metric-score-box" },
          el("span", { className: "score" }, hasRun ? formatScore(row.score) : "–"),
          el("span", { className: "threshold" }, `≥ ${formatScore(metric.threshold)}`),
        ),
    el(
      "p",
      { className: "metric-card-meta muted" },
      isRunning
        ? "scoring each golden answer against the judge…"
        : [goldenText, hasRun ? formatRunTime(row.ts) : "not run yet"].filter(Boolean).join(" · "),
    ),
    el(
      "div",
      { className: "metric-card-actions" },
      el(
        "button",
        {
          type: "button",
          className: "run-metric",
          disabled: !state.judgeUp || !currentTarget() || isRunning || state.running,
          onclick: () => runSingleMetric(metric.key),
        },
        isRunning ? "▸ Running…" : "▸ Run",
      ),
      el(
        "button",
        {
          type: "button",
          onclick: () => toggleCardDetails(metric.key),
        },
        state.expandedDetailKeys.has(metric.key) ? "Hide details" : "Details",
      ),
      el(
        "button",
        {
          type: "button",
          onclick: () => {
            $("metric-select").value = metric.key;
            settings.set("selectedMetric", metric.key);
            setView("history");
          },
        },
        "Trend",
      ),
    ),
    state.expandedDetailKeys.has(metric.key) ? caseDetailPanel(metric, state.caseDetailsCache[metric.key]) : null,
  );
}

function renderCategoryChips() {
  const categories = [...new Set(state.metrics.map((m) => m.category))];
  const items = ["all", ...categories];
  $("category-chips").replaceChildren(
    ...items.map((cat) =>
      el(
        "button",
        {
          type: "button",
          className: "chip-toggle",
          "aria-pressed": String(cat === state.categoryFilter),
          onclick: () => {
            state.categoryFilter = cat;
            renderCategoryChips();
            renderMetricGrid();
          },
        },
        cat === "all" ? "All" : CATEGORY_LABELS[cat] || cat,
      ),
    ),
  );
}

async function runSingleMetric(key) {
  const target = currentTarget();
  if (!target) return;
  state.running = true;
  updateRunButton();
  try {
    await runMetrics(target, [key], (_key, result) => {
      const title = state.metrics.find((m) => m.key === key)?.title || key;
      if (result === null) {
        state.runningKeys.add(key);
        setStatus(`Running ${title}… (this calls the real judge model per golden case, can take a while)`);
      } else {
        state.runningKeys.delete(key);
        refreshExpandedDetail(key, result);
        if (result.status === "error") setStatus(`${title}: ${result.error}`, true);
        else setStatus(`${title}: ${formatScore(result.score)} ${result.status} (${result.cases_run} cases)`);
      }
      renderMetricGrid();
    });
  } finally {
    state.running = false;
    state.runningKeys.delete(key);
    updateRunButton();
    renderMetricGrid();
  }
}

// Metric card grid: the dashboard's main "what's judged, what isn't" view.
// Replaces the old KPI tiles + single latest-scores chart with one card per
// metric, filterable by category, each runnable on its own.
let latestRowsCache = [];

function renderMetricGrid() {
  const shown = state.metrics.filter((m) => state.categoryFilter === "all" || m.category === state.categoryFilter);
  const byKey = new Map(latestRowsCache.map((r) => [r.metric_key, r]));
  $("metric-grid-empty").classList.toggle("hidden", state.metrics.length > 0);
  $("metric-grid").replaceChildren(...shown.map((m) => metricCard(m, byKey.get(m.key))));
}

async function renderLatest() {
  const target = currentTarget();
  if (!target) return;
  try {
    latestRowsCache = await api(`/api/runs/latest?target_id=${target.id}`);
    renderStatusStrip(latestRowsCache);
    renderMetricGrid();
  } catch (error) {
    setStatus(error.message, true);
  }
}

function renderTrendTable(historyByMetric, metrics) {
  const shown = metrics.filter((m) => historyByMetric[m.key].length > 0);
  const times = [...new Set(shown.flatMap((m) => historyByMetric[m.key].map((r) => r.ts)))]
    .sort()
    .reverse();
  const table = $("trend-table");
  table.tHead.replaceChildren(
    el("tr", {}, el("th", {}, "Run time"), ...shown.map((m) => el("th", { className: "num" }, m.title))),
  );
  table.tBodies[0].replaceChildren(
    ...times.map((ts) =>
      el(
        "tr",
        {},
        el("td", {}, formatRunTime(ts)),
        ...shown.map((m) => {
          const run = historyByMetric[m.key].find((r) => r.ts === ts);
          const text = run ? `${formatScore(run.score)} ${run.passed ? "✓" : "✕"}` : "–";
          return el("td", { className: "num" }, text);
        }),
      ),
    ),
  );
}

async function renderTrend() {
  const target = currentTarget();
  if (!target || state.metrics.length === 0) return;
  const choice = $("metric-select").value;
  const metrics =
    choice === ALL_METRICS ? state.metrics : state.metrics.filter((m) => m.key === choice);
  $("trend-heading").textContent =
    choice === ALL_METRICS
      ? "Score trend across runs · all metrics"
      : `Score trend across runs · ${metrics[0] ? metrics[0].title : choice}`;
  const box = $("trend-chart-box");
  box.classList.add("loading");
  try {
    const histories = await Promise.all(
      metrics.map((m) =>
        api(`/api/history?target_id=${target.id}&metric_key=${encodeURIComponent(m.key)}`),
      ),
    );
    const historyByMetric = Object.fromEntries(metrics.map((m, i) => [m.key, histories[i]]));
    const hasRuns = histories.some((h) => h.length > 0);
    const showTable = state.trendView === "table";
    $("trend-empty").classList.toggle("hidden", hasRuns);
    $("trend-table").classList.toggle("hidden", !hasRuns || !showTable);
    box.classList.toggle("hidden", !hasRuns || showTable);
    renderTrendTable(historyByMetric, metrics);
    if (hasRuns && !showTable) {
      state.trendChart = drawChart(
        $("trend-chart"),
        state.trendChart,
        trendChartConfig(historyByMetric, state.metrics),
      );
    }
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    box.classList.remove("loading");
  }
}

// Full run log for the current chatbot: every recorded run, all metrics,
// newest first. "Latest score per metric" only ever shows the most recent
// point per metric, so this is the only place the full record lives.
async function renderHistory() {
  const target = currentTarget();
  if (!target || state.metrics.length === 0) return;
  const choice = $("metric-select").value;
  const metrics = choice === ALL_METRICS ? state.metrics : state.metrics.filter((m) => m.key === choice);
  const byKey = new Map(state.metrics.map((m) => [m.key, m]));
  const table = $("history-table");
  const empty = $("history-empty");
  table.classList.add("hidden");
  try {
    const histories = await Promise.all(
      metrics.map((m) => api(`/api/history?target_id=${target.id}&metric_key=${encodeURIComponent(m.key)}`)),
    );
    const rows = histories.flat().sort((a, b) => (a.ts < b.ts ? 1 : -1));
    $("history-count").textContent = rows.length ? `(${rows.length} run${rows.length > 1 ? "s" : ""})` : "";
    empty.classList.toggle("hidden", rows.length > 0);
    table.classList.toggle("hidden", rows.length === 0);
    table.tBodies[0].replaceChildren(
      ...rows.map((r) =>
        el(
          "tr",
          {},
          el("td", {}, formatRunTime(r.ts)),
          el("td", {}, byKey.get(r.metric_key)?.title ?? r.metric_key),
          el("td", { className: "num" }, formatScore(r.score)),
          el("td", { className: "num" }, formatScore(byKey.get(r.metric_key)?.threshold)),
          el("td", { className: r.passed ? "status-pass" : "status-fail" }, resultLabel(r.passed)),
        ),
      ),
    );
  } catch (error) {
    setStatus(error.message, true);
  }
}

function setView(view) {
  state.view = view;
  $("judge-view").classList.toggle("hidden", view !== "judge");
  $("history-view").classList.toggle("hidden", view !== "history");
  for (const button of document.querySelectorAll("[data-view]")) {
    button.setAttribute("aria-pressed", String(button.dataset.view === view));
  }
  if (view === "history") renderHistory().catch((error) => setStatus(error.message, true));
}

function renderCases(lastRun) {
  const target = currentTarget();
  const table = $("cases-table");
  const empty = $("cases-empty");
  const result = lastRun && target && lastRun.targetId === target.id ? lastRun.result : null;
  const metric = result ? state.metrics.find((m) => m.key === lastRun.metricKey) : null;
  $("cases-heading").textContent = result
    ? `Last run, case by case · ${metric ? metric.title : lastRun.metricKey}`
    : "Last run, case by case";
  if (!result || result.status === "error") {
    table.classList.add("hidden");
    empty.classList.remove("hidden");
    empty.className = result ? "status-error" : "muted";
    empty.textContent = result
      ? `The run failed: ${result.error}`
      : "Run the judge to see how each golden answer scored.";
    return;
  }
  empty.classList.add("hidden");
  table.classList.remove("hidden");
  table.tBodies[0].replaceChildren(
    ...result.rows.map((row) =>
      el(
        "tr",
        {},
        el("td", {}, row.question),
        el("td", {}, row.actual_output),
        el("td", { className: "num" }, formatScore(row.score)),
        el("td", { className: row.passed ? "status-pass" : "status-fail" }, resultLabel(row.passed)),
        el("td", {}, row.reason || "–"),
      ),
    ),
  );
}

$("chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const target = currentTarget();
  const input = $("chat-input");
  const message = input.value.trim();
  if (!target || !message) return;
  input.value = "";
  appendBubble("user", message);
  input.disabled = true;
  try {
    const reply = await api("/api/chat", {
      method: "POST",
      body: { target_id: target.id, message, history: state.history.slice(-40) },
    });
    const model = reply.model && reply.model !== "unknown" ? ` · ${reply.model}` : "";
    appendBubble("bot", reply.reply, `${target.name} · ${reply.mode}${model}`, {
      question: message,
      actual_output: reply.reply,
    });
    state.history.push({ role: "user", content: message }, { role: "assistant", content: reply.reply });
  } catch (error) {
    appendBubble("error", error.message);
  } finally {
    input.disabled = false;
    input.focus();
  }
});

$("run-button").addEventListener("click", async () => {
  const target = currentTarget();
  if (!target) return;
  const choice = $("metric-select").value;
  const keys = choice === ALL_METRICS ? state.metrics.map((m) => m.key) : [choice];
  const titles = new Map(state.metrics.map((m) => [m.key, m.title]));
  state.running = true;
  updateRunButton();
  try {
    const results = await runMetrics(target, keys, (key, result) => {
      const title = titles.get(key) || key;
      if (result === null) {
        state.runningKeys.add(key);
        setStatus(`Running ${title}… (real judge calls per golden case, can take a while)`);
      } else {
        state.runningKeys.delete(key);
        refreshExpandedDetail(key, result);
        if (result.status === "error") setStatus(`${title}: ${result.error}`, true);
        else setStatus(`${title}: ${formatScore(result.score)} ${result.status} (${result.cases_run} cases)`);
      }
      renderMetricGrid();
    });
    const failed = results.filter((r) => r.status === "error").length;
    if (keys.length > 1) {
      setStatus(
        `Finished ${keys.length} metrics${failed ? ` · ${failed} could not run` : ""}.`,
        failed > 0,
      );
    }
  } finally {
    state.running = false;
    for (const key of keys) state.runningKeys.delete(key);
    updateRunButton();
    renderMetricGrid();
  }
});

for (const button of document.querySelectorAll("[data-view]")) {
  button.addEventListener("click", () => setView(button.dataset.view));
}

for (const button of document.querySelectorAll("[data-trend-view]")) {
  button.addEventListener("click", () => {
    state.trendView = button.dataset.trendView;
    for (const other of document.querySelectorAll("[data-trend-view]")) {
      other.setAttribute("aria-pressed", String(other === button));
    }
    renderTrend();
  });
}

$("target-select").addEventListener("change", () => {
  onTargetChanged().catch((error) => setStatus(error.message, true));
});

$("metric-select").addEventListener("change", () => {
  settings.set("selectedMetric", $("metric-select").value);
  renderTrend();
  if (state.view === "history") renderHistory();
});

settings.onChange((changes) => {
  const target = currentTarget();
  const run = changes.lastRun && changes.lastRun.newValue;
  const wasRun = changes.lastRun && changes.lastRun.oldValue;
  // Also fires when lastRun is cleared (Reset scores): newValue is null, but
  // oldValue still names the target whose cards need to go back to "not run".
  const affectsThisTarget = target && ((run && run.targetId === target.id) || (!run && wasRun && wasRun.targetId === target.id));
  if (affectsThisTarget) {
    renderCases(run || null);
    renderLatest();
    renderTrend();
    if (state.view === "history") renderHistory();
  }
  if (changes.selectedTargetId && changes.selectedTargetId.newValue !== (target ? target.id : null)) {
    loadTargets(changes.selectedTargetId.newValue).catch((error) => setStatus(error.message, true));
  }
  if (changes.selectedMetric && changes.selectedMetric.newValue !== $("metric-select").value) {
    $("metric-select").value = changes.selectedMetric.newValue;
    renderTrend();
    if (state.view === "history") renderHistory();
  }
});

window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
  renderLatest();
  renderTrend();
});

window.addEventListener("focus", () => {
  refreshJudge();
  renderGoldenCount();
});

(async function init() {
  await refreshJudge();
  try {
    state.metrics = await api("/api/metrics");
    const savedMetric = await settings.get("selectedMetric", ALL_METRICS);
    fillSelect($("metric-select"), metricOptions(state.metrics), savedMetric);
    renderCategoryChips();
    await loadTargets();
  } catch (error) {
    setStatus(`${error.message} Reload this tab once the backend is running.`, true);
  }
})();
