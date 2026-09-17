// Side panel: pick or add a chatbot, manage its golden answers, run the judge,
// and see the latest scores. The dashboard tab follows the same selection
// through chrome.storage.

const $ = (id) => document.getElementById(id);
const state = {
  targets: [],
  metrics: [],
  backendUp: false,
  judgeUp: false,
  activeTab: null,
  chart: null,
  editingGolden: null,
  editingTarget: null,
};

function currentTarget() {
  const id = Number($("target-select").value);
  return state.targets.find((t) => t.id === id) || null;
}

function updateRunButton() {
  const button = $("run-button");
  const ready = state.judgeUp && currentTarget() !== null;
  button.disabled = !ready;
  if (ready) button.title = "";
  else if (state.judgeUp) button.title = "Add or pick a chatbot first";
  else button.title = "Judge not configured: JUDGE_API_KEY is missing";
}

async function refreshStatus() {
  try {
    const status = await api("/api/status");
    state.backendUp = true;
    state.judgeUp = status.judge.up;
    $("status").textContent = state.judgeUp
      ? `Judge ready · ${status.judge.model}`
      : "Judge not configured: add JUDGE_API_KEY to .env, then restart the backend.";
  } catch (error) {
    state.backendUp = false;
    state.judgeUp = false;
    $("status").textContent = error.message;
  }
  updateRunButton();
}

async function loadMetrics() {
  state.metrics = await api("/api/metrics");
  const saved = await settings.get("selectedMetric", ALL_METRICS);
  fillSelect($("metric-select"), metricOptions(state.metrics), saved);
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
  $("target-theme").textContent = target ? `Golden set: ${themeOf(target)}` : "";
  $("delete-target").disabled = !target;
  $("edit-target").disabled = !target;
  $("reset-target-runs").disabled = !target;
  $("run-results").replaceChildren();
  // Goldens belong to the target's theme, so drop any half-finished edit.
  resetGoldenForm();
  // A half-finished target edit refers to whichever target was selected
  // before; the selection just changed, so that edit no longer applies.
  resetTargetForm();
  updateRunButton();
  await Promise.all([loadGoldens(), renderLatest()]);
}

// The target form doubles as the editor; resetTargetForm returns it to "add".
function resetTargetForm() {
  state.editingTarget = null;
  $("target-form").reset();
  syncTargetForm();
  $("target-submit").textContent = "Save chatbot";
  $("target-cancel").classList.add("hidden");
  $("target-form-summary").textContent = "Add a chatbot";
  $("target-api-key-hint").classList.add("hidden");
}

// The backend only stores the expanded HTTP config, not which preset built
// it — commandcode and openai both expand to the same shape, so either one
// maps back to "openai" (picking a preset again just re-derives that shape).
function targetUiType(target) {
  if (target.type === "mock" || target.type === "dom") return target.type;
  return target.config.request_format === "openai_messages" ? "openai" : "http";
}

function startEditTarget(target) {
  state.editingTarget = target.id;
  $("target-name").value = target.name;
  $("target-type").value = targetUiType(target);
  syncTargetForm();
  $("target-theme-input").value = themeOf(target);
  $("target-api-key").value = "";
  $("target-base-url").value = target.config.base_url || "";
  $("target-model").value = target.config.model || "gpt-4o-mini";
  $("target-chat-path").value = target.config.chat_path || "/chat";
  $("target-message-field").value = target.config.message_field || "message";
  $("target-response-path").value = target.config.response_path || "reply";
  $("target-submit").textContent = "Save changes";
  $("target-cancel").classList.remove("hidden");
  $("target-form-summary").textContent = "Edit chatbot";
  $("target-form-error").textContent = "";
  $("target-api-key-hint").classList.toggle("hidden", targetUiType(target) === "mock" || targetUiType(target) === "dom");
  $("add-target").open = true;
  $("target-name").focus();
}

// The golden form doubles as the editor; resetGoldenForm returns it to "add".
function resetGoldenForm() {
  state.editingGolden = null;
  $("golden-form").reset();
  $("golden-submit").textContent = "Add golden answer";
  $("golden-cancel").classList.add("hidden");
  $("golden-form-summary").textContent = "Add a golden answer";
}

function startEditGolden(golden) {
  state.editingGolden = golden.id;
  $("golden-question").value = golden.question;
  $("golden-answer").value = golden.expected_answer;
  $("golden-context").value = (golden.context || []).join("\n");
  $("golden-categories").value = (golden.categories || []).join(", ");
  $("golden-submit").textContent = "Save changes";
  $("golden-cancel").classList.remove("hidden");
  $("golden-form-summary").textContent = "Edit golden answer";
  $("golden-form-error").textContent = "";
  $("add-golden").open = true;
  $("golden-question").focus();
}

async function loadGoldens() {
  const target = currentTarget();
  const list = $("golden-list");
  if (!target) {
    list.replaceChildren();
    $("golden-count").textContent = "";
    return;
  }
  let goldens = [];
  try {
    goldens = await api(`/api/goldens?theme=${encodeURIComponent(themeOf(target))}`);
  } catch (error) {
    $("golden-form-error").textContent = error.message;
  }
  $("golden-count").textContent = `(${goldens.length})`;
  if (goldens.length === 0) {
    list.replaceChildren(
      el("li", { className: "muted" }, "No golden answers in this set yet. Add at least one; the judge scores against them."),
    );
    return;
  }
  list.replaceChildren(
    ...goldens.map((golden) =>
      el(
        "li",
        {},
        el(
          "div",
          { className: "text", title: `${golden.question}\n\n${golden.expected_answer}` },
          el("div", { className: "question" }, golden.question),
          el("div", { className: "answer" }, golden.expected_answer),
        ),
        el("button", {
          type: "button",
          "aria-label": `Edit golden answer: ${golden.question}`,
          onclick: () => startEditGolden(golden),
        }, "Edit"),
        confirmButton("Delete", `Delete golden answer: ${golden.question}`, async () => {
          try {
            await api(`/api/goldens/${encodeURIComponent(golden.id)}`, { method: "DELETE" });
          } catch (error) {
            $("golden-form-error").textContent = error.message;
          }
          if (state.editingGolden === golden.id) resetGoldenForm();
          await loadGoldens();
        }),
      ),
    ),
  );
}

async function renderLatest() {
  const target = currentTarget();
  const box = $("latest-chart-box");
  if (!target) return;
  box.classList.add("loading");
  try {
    const rows = await api(`/api/runs/latest?target_id=${target.id}`);
    const hasRuns = rows.length > 0;
    box.classList.toggle("hidden", !hasRuns);
    $("latest-empty").classList.toggle("hidden", hasRuns);
    if (hasRuns) {
      box.style.height = `${rows.length * 30 + 80}px`;
      state.chart = drawChart($("latest-chart"), state.chart, latestChartConfig(rows, state.metrics, "bar"));
    } else if (state.chart) {
      state.chart.destroy();
      state.chart = null;
    }
  } catch (error) {
    $("status").textContent = error.message;
  } finally {
    box.classList.remove("loading");
  }
}

function syncTargetForm() {
  const type = $("target-type").value;
  for (const block of document.querySelectorAll("#target-form [data-for]")) {
    block.classList.toggle("hidden", !block.dataset.for.split(" ").includes(type));
  }
}

// Each provider suggests its own model id; follow it when the user switches
// providers. They can still type any model id they like afterwards.
function applySuggestedModel() {
  const option = $("target-type").selectedOptions[0];
  if (option && option.dataset.model) $("target-model").value = option.dataset.model;
}

function buildTargetBody(type, name, theme) {
  const value = (id) => $(id).value.trim();
  if (type === "mock") {
    return { name, type: "mock", config: { theme } };
  }
  if (type === "commandcode") {
    const apiKey = value("target-api-key");
    if (!apiKey) throw new Error("API key is required for Command Code.");
    const config = { api_key: apiKey, theme };
    const model = value("target-model");
    if (model) config.model = model;
    return { name, type: "http", preset: "commandcode", config };
  }
  const baseUrl = value("target-base-url");
  if (!baseUrl) throw new Error("Base URL is required.");
  if (type === "openai") {
    const apiKey = value("target-api-key");
    if (!apiKey) throw new Error("API key is required for an OpenAI-compatible API.");
    return {
      name,
      type: "http",
      preset: "openai_compatible",
      config: { base_url: baseUrl, api_key: apiKey, model: value("target-model") || "gpt-4o-mini", theme },
    };
  }
  if (type === "http") {
    const config = {
      base_url: baseUrl,
      chat_path: value("target-chat-path") || "/chat",
      message_field: value("target-message-field") || "message",
      response_path: value("target-response-path") || "reply",
      theme,
    };
    const apiKey = value("target-api-key");
    if (apiKey) config.headers = { Authorization: `Bearer ${apiKey}` };
    return { name, type: "http", config };
  }
  throw new Error(`Unknown chatbot type: ${type}`);
}

async function trackActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  state.activeTab = tab || null;
}

function saveDomTarget(name, theme, targetId) {
  const tab = state.activeTab;
  let url = null;
  try {
    url = new URL(tab && tab.url);
  } catch {
    url = null;
  }
  if (!url || !/^https?:$/.test(url.protocol)) {
    return Promise.reject(new Error("Open the chatbot's web page (http or https) in the current tab first."));
  }
  const origin = `${url.protocol}//${url.hostname}/*`;
  // permissions.request must be the first async call so Chrome still sees the click.
  return chrome.permissions.request({ origins: [origin] }).then(async (granted) => {
    if (!granted) throw new Error(`Permission for ${url.hostname} was declined.`);
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content_script.js"] });
    const body = { name, type: "dom", config: { session_id: url.hostname, theme } };
    return targetId
      ? api(`/api/targets/${targetId}`, { method: "PUT", body })
      : api("/api/targets", { method: "POST", body });
  });
}

$("target-form").addEventListener("submit", (event) => {
  // Not async on purpose: a "web page" chatbot must request its permission
  // synchronously inside this click.
  event.preventDefault();
  $("target-form-error").textContent = "";
  const type = $("target-type").value;
  const name = $("target-name").value.trim();
  const theme = $("target-theme-input").value.trim() || DEFAULT_THEME;
  const editingId = state.editingTarget;
  let saving;
  try {
    if (type === "dom") {
      saving = saveDomTarget(name, theme, editingId);
    } else {
      const body = buildTargetBody(type, name, theme);
      saving = editingId
        ? api(`/api/targets/${editingId}`, { method: "PUT", body })
        : api("/api/targets", { method: "POST", body });
    }
  } catch (error) {
    saving = Promise.reject(error);
  }
  saving
    .then(async (saved) => {
      resetTargetForm();
      $("add-target").open = false;
      await loadTargets(saved.id);
    })
    .catch((error) => {
      $("target-form-error").textContent = error.message;
    });
});

$("target-cancel").addEventListener("click", () => {
  resetTargetForm();
  $("target-form-error").textContent = "";
  $("add-target").open = false;
});

$("edit-target").addEventListener("click", () => {
  const target = currentTarget();
  if (target) startEditTarget(target);
});

$("golden-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("golden-form-error").textContent = "";
  const target = currentTarget();
  if (!target) return;
  const context = $("golden-context").value.split("\n").map((s) => s.trim()).filter(Boolean);
  const categories = $("golden-categories").value.split(",").map((s) => s.trim()).filter(Boolean);
  const editing = state.editingGolden;
  const body = {
    theme: themeOf(target),
    question: $("golden-question").value.trim(),
    expected_answer: $("golden-answer").value.trim(),
    context,
    categories,
  };
  try {
    if (editing) {
      await api(`/api/goldens/${encodeURIComponent(editing)}`, { method: "PUT", body });
    } else {
      await api("/api/goldens", { method: "POST", body });
    }
    resetGoldenForm();
    $("add-golden").open = false;
    await loadGoldens();
  } catch (error) {
    $("golden-form-error").textContent = error.message;
  }
});

$("golden-cancel").addEventListener("click", () => {
  resetGoldenForm();
  $("golden-form-error").textContent = "";
  $("add-golden").open = false;
});

armConfirm($("delete-target"), async () => {
  const target = currentTarget();
  if (!target) return;
  try {
    await api(`/api/targets/${target.id}`, { method: "DELETE" });
    await loadTargets(null);
  } catch (error) {
    $("status").textContent = error.message;
  }
});

// Clears only this target's run history (goldens and config are untouched) —
// mainly for after Edit changes a target's type, when old scores describe a
// chatbot that no longer exists under this id.
armConfirm($("reset-target-runs"), async () => {
  const target = currentTarget();
  if (!target) return;
  try {
    await api(`/api/targets/${target.id}/runs`, { method: "DELETE" });
    const lastRun = await settings.get("lastRun", null);
    if (lastRun && lastRun.targetId === target.id) await settings.set("lastRun", null);
    await clearCaseDetails(target.id);
    await renderLatest();
  } catch (error) {
    $("status").textContent = error.message;
  }
});

// One score-bar row per metric: bar fill mirrors the dashboard's chart bars,
// a chip on the right gives the pass/fail read at a glance.
function renderRunRow(line, title, result) {
  if (result === null) {
    line.className = "run-row";
    line.replaceChildren(
      el("span", { className: "run-row-title" }, title),
      el("span", { className: "run-bar running" }, el("span", { className: "run-bar-fill" })),
      el("span", { className: "run-row-chip muted" }, "running…"),
    );
    return;
  }
  if (result.status === "error") {
    line.className = "run-row";
    line.replaceChildren(
      el("span", { className: "run-row-title" }, title),
      el("span", { className: "run-row-chip status-error" }, `! ${result.error}`),
    );
    return;
  }
  const pct = Math.max(0, Math.min(1, result.score ?? 0)) * 100;
  line.className = `run-row status-${result.status}`;
  line.replaceChildren(
    el("span", { className: "run-row-title" }, title),
    el("span", { className: "run-bar" }, el("span", { className: "run-bar-fill", style: `width:${pct}%` })),
    el(
      "span",
      { className: "run-row-chip" },
      `${formatScore(result.score)} ${result.status === "pass" ? "✓" : "✕"} (${result.cases_run} cases)`,
    ),
  );
}

$("run-button").addEventListener("click", async () => {
  const target = currentTarget();
  if (!target) return;
  const choice = $("metric-select").value;
  const keys = choice === ALL_METRICS ? state.metrics.map((m) => m.key) : [choice];
  const titles = new Map(state.metrics.map((m) => [m.key, m.title]));
  const lines = new Map();
  $("run-results").replaceChildren();
  $("run-button").disabled = true;
  try {
    await runMetrics(target, keys, (key, result) => {
      if (!lines.has(key)) {
        lines.set(key, el("li"));
        $("run-results").append(lines.get(key));
      }
      renderRunRow(lines.get(key), titles.get(key) || key, result);
    });
  } finally {
    updateRunButton();
  }
});

$("target-select").addEventListener("change", () => {
  onTargetChanged().catch((error) => {
    $("status").textContent = error.message;
  });
});
$("metric-select").addEventListener("change", () => settings.set("selectedMetric", $("metric-select").value));
$("target-type").addEventListener("change", () => {
  applySuggestedModel();
  syncTargetForm();
});
$("open-dashboard").addEventListener("click", () => chrome.runtime.sendMessage({ type: "OPEN_DASHBOARD" }));
chrome.tabs.onActivated.addListener(() => trackActiveTab());
chrome.tabs.onUpdated.addListener((_tabId, _info, tab) => {
  if (tab.active) trackActiveTab();
});
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => renderLatest());

settings.onChange((changes) => {
  const target = currentTarget();
  const run = changes.lastRun && changes.lastRun.newValue;
  if (run && target && run.targetId === target.id) renderLatest();
  if (changes.selectedTargetId && changes.selectedTargetId.newValue !== (target ? target.id : null)) {
    loadTargets(changes.selectedTargetId.newValue).catch((error) => {
      $("status").textContent = error.message;
    });
  }
  if (changes.selectedMetric && changes.selectedMetric.newValue !== $("metric-select").value) {
    $("metric-select").value = changes.selectedMetric.newValue;
  }
});

async function loadEverything() {
  await refreshStatus();
  if (!state.backendUp) return false;
  try {
    await loadMetrics();
    await loadTargets();
    return true;
  } catch (error) {
    $("status").textContent = error.message;
    return false;
  }
}

(async function init() {
  syncTargetForm();
  trackActiveTab();
  let loaded = await loadEverything();
  // Keep checking, so the panel recovers once the backend is started.
  setInterval(async () => {
    if (loaded) await refreshStatus();
    else loaded = await loadEverything();
  }, 10000);
})();
