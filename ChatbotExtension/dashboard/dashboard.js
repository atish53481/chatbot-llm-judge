// Dashboard tab: chat with the selected chatbot, run the judge, and read the
// latest scores, the trend across runs, and the last run case by case.

const $ = (id) => document.getElementById(id);
const state = {
  targets: [],
  metrics: [],
  judgeUp: false,
  running: false,
  latestView: "bar",
  trendView: "chart",
  latestChart: null,
  trendChart: null,
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

function appendBubble(kind, text, meta) {
  const log = $("chat-log");
  log.append(
    el("div", { className: `bubble ${kind}` }, text, meta ? el("span", { className: "meta" }, meta) : null),
  );
  log.scrollTop = log.scrollHeight;
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
  if (target) appendBubble("note", `Chatting with ${target.name}. Golden set: ${themeOf(target)}.`);
  updateRunButton();
  renderCases(await settings.get("lastRun", null));
  await Promise.all([renderLatest(), renderTrend(), renderGoldenCount()]);
}

async function renderGoldenCount() {
  const target = currentTarget();
  if (!target) return;
  try {
    const goldens = await api(`/api/goldens?theme=${encodeURIComponent(themeOf(target))}`);
    $("kpi-goldens").textContent = String(goldens.length);
  } catch {
    $("kpi-goldens").textContent = "–";
  }
}

function renderKpis(rows) {
  const passing = rows.filter((r) => r.passed).length;
  const scores = rows.map((r) => r.score).filter((s) => typeof s === "number");
  const last = rows.map((r) => r.ts).sort().at(-1);
  $("kpi-passing").textContent = rows.length ? `${passing}/${rows.length}` : "–";
  $("kpi-average").textContent = scores.length
    ? (scores.reduce((sum, s) => sum + s, 0) / scores.length).toFixed(2)
    : "–";
  $("kpi-last-run").textContent = last ? formatRunTime(last) : "–";
}

function renderLatestTable(rows) {
  const byKey = new Map(state.metrics.map((m) => [m.key, m]));
  $("latest-table").tBodies[0].replaceChildren(
    ...rows.map((r) =>
      el(
        "tr",
        {},
        el("td", {}, byKey.get(r.metric_key)?.title ?? r.metric_key),
        el("td", { className: "num" }, formatScore(r.score)),
        el("td", { className: "num" }, formatScore(byKey.get(r.metric_key)?.threshold)),
        el("td", { className: r.passed ? "status-pass" : "status-fail" }, resultLabel(r.passed)),
        el("td", {}, formatRunTime(r.ts)),
      ),
    ),
  );
}

async function renderLatest() {
  const target = currentTarget();
  if (!target) return;
  const box = $("latest-chart-box");
  box.classList.add("loading");
  try {
    const rows = await api(`/api/runs/latest?target_id=${target.id}`);
    const ordered = state.metrics
      .map((m) => rows.find((r) => r.metric_key === m.key))
      .filter(Boolean);
    renderKpis(ordered);
    renderLatestTable(ordered);
    const hasRuns = ordered.length > 0;
    const showTable = state.latestView === "table";
    $("latest-empty").classList.toggle("hidden", hasRuns);
    $("latest-table").classList.toggle("hidden", !hasRuns || !showTable);
    box.classList.toggle("hidden", !hasRuns || showTable);
    if (hasRuns && !showTable) {
      box.style.height = state.latestView === "radar" ? "360px" : `${ordered.length * 34 + 90}px`;
      state.latestChart = drawChart(
        $("latest-chart"),
        state.latestChart,
        latestChartConfig(ordered, state.metrics, state.latestView),
      );
    }
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    box.classList.remove("loading");
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
      body: { target_id: target.id, message },
    });
    const model = reply.model && reply.model !== "unknown" ? ` · ${reply.model}` : "";
    appendBubble("bot", reply.reply, `${target.name} · ${reply.mode}${model}`);
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
      if (result === null) setStatus(`Running ${title}…`);
      else if (result.status === "error") setStatus(`${title}: ${result.error}`, true);
      else setStatus(`${title}: ${formatScore(result.score)} ${result.status} (${result.cases_run} cases)`);
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
    updateRunButton();
  }
});

for (const button of document.querySelectorAll("[data-latest-view]")) {
  button.addEventListener("click", () => {
    state.latestView = button.dataset.latestView;
    for (const other of document.querySelectorAll("[data-latest-view]")) {
      other.setAttribute("aria-pressed", String(other === button));
    }
    renderLatest();
  });
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
});

settings.onChange((changes) => {
  const target = currentTarget();
  const run = changes.lastRun && changes.lastRun.newValue;
  if (run && target && run.targetId === target.id) {
    renderCases(run);
    renderLatest();
    renderTrend();
  }
  if (changes.selectedTargetId && changes.selectedTargetId.newValue !== (target ? target.id : null)) {
    loadTargets(changes.selectedTargetId.newValue).catch((error) => setStatus(error.message, true));
  }
  if (changes.selectedMetric && changes.selectedMetric.newValue !== $("metric-select").value) {
    $("metric-select").value = changes.selectedMetric.newValue;
    renderTrend();
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
    await loadTargets();
  } catch (error) {
    setStatus(`${error.message} Reload this tab once the backend is running.`, true);
  }
})();
