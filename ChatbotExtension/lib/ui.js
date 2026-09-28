// Helpers shared by the side panel and the dashboard. Everything that comes
// from a chatbot or the backend is inserted as text, never as HTML.

const ALL_METRICS = "__all__";
const DEFAULT_THEME = "generic";
// Thresholds per metric live in chrome.storage.local ("thresholds": {key: value});
// a metric with none set uses its catalog default. "thresholdEnv" names the
// preset they came from (default / local / pr / staging / production / custom).
const THRESHOLD_ENVS = [
  { key: "default", label: "Default (per metric)" },
  { key: "local", label: "Local Development" },
  { key: "pr", label: "PR / Feature Branch" },
  { key: "staging", label: "Staging / QA" },
  { key: "production", label: "Production" },
  { key: "custom", label: "Custom" },
];
const GROUP_PREFIX = "group:";

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "className") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2).toLowerCase(), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

function fillSelect(select, items, selectedValue) {
  select.replaceChildren(
    ...items.map(({ value, label }) => {
      const option = el("option", { value }, label);
      option.selected = String(value) === String(selectedValue);
      return option;
    }),
  );
}

// Turns a button into a two-click delete (no native dialogs in the side panel).
function armConfirm(button, onConfirm) {
  const label = button.textContent;
  let timer = null;
  button.addEventListener("click", async () => {
    if (!timer) {
      button.textContent = "Confirm";
      timer = setTimeout(() => {
        timer = null;
        button.textContent = label;
      }, 3000);
      return;
    }
    clearTimeout(timer);
    timer = null;
    button.textContent = label;
    button.disabled = true;
    try {
      await onConfirm();
    } finally {
      button.disabled = false;
    }
  });
  return button;
}

function confirmButton(label, ariaLabel, onConfirm) {
  return armConfirm(el("button", { type: "button", "aria-label": ariaLabel }, label), onConfirm);
}

function formatScore(score) {
  return typeof score === "number" ? score.toFixed(2) : "n/a";
}

function formatTokens(tokens) {
  if (typeof tokens !== "number") return "";
  return tokens >= 1000 ? `${(tokens / 1000).toFixed(1)}k` : String(tokens);
}

// "2 skipped (too long) · 14.2k tokens · ⚠ judge unstable (0.40)": how a finished run was judged.
function judgingSummary(result) {
  const parts = [];
  if (result.cases_skipped) parts.push(`${result.cases_skipped} skipped (too long)`);
  if (result.judge && typeof result.judge.tokens === "number") parts.push(`${formatTokens(result.judge.tokens)} tokens`);
  if (result.judge_unstable) parts.push(`⚠ judge unstable (${formatScore(result.judge_spread)})`);
  return parts.join(" · ");
}

function formatRunTime(ts) {
  // Backend timestamps are ISO-8601 UTC, e.g. "2026-09-16T14:32:07Z".
  const date = new Date(ts);
  if (Number.isNaN(date.getTime())) return String(ts);
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function resultLabel(passed) {
  return passed ? "✓ pass" : "✕ fail";
}

function themeOf(target) {
  return (target && target.config && target.config.theme) || DEFAULT_THEME;
}

function targetLabel(target) {
  return target.name;
}

// "Metric 2/7 · 3/10 · judging: What is your refund window? · 0:42" for a run in flight.
function progressText(progress) {
  if (!progress) return "starting…";
  const parts = [];
  if (progress.metricCount > 1) parts.push(`metric ${progress.metricIndex + 1}/${progress.metricCount}`);
  if (progress.total) {
    const doing = progress.phase === "judge" ? "judging" : "asking chatbot";
    parts.push(`${progress.done}/${progress.total} · ${doing}: ${progress.question}`);
  } else {
    parts.push("starting…");
  }
  const seconds = Math.round((Date.now() - progress.startedAt) / 1000);
  parts.push(`${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`);
  return parts.join(" · ");
}

// 0..1 through the current metric: each golden counts half for the chatbot, half for the judge.
function progressFraction(progress) {
  if (!progress || !progress.total) return 0;
  return (progress.done + (progress.phase === "judge" ? 0.5 : 0)) / progress.total;
}

// Metrics in catalog order, grouped: [{group, label, metrics}].
// Grouped by the dashboard categories (Quality, Retrieval, Safety, Security,
// G-Eval, Conversational), so the side panel and the dashboard match; the
// backend already sends the metrics in that order.
function metricGroups(metrics) {
  const groups = [];
  for (const metric of metrics) {
    let group = groups.find((g) => g.group === metric.ui_category);
    if (!group) {
      group = { group: metric.ui_category, label: metric.ui_category_label || metric.ui_category, metrics: [] };
      groups.push(group);
    }
    group.metrics.push(metric);
  }
  return groups;
}

// The metric picker: "All metrics", then per group "All · <group>" and its metrics.
function fillMetricSelect(select, metrics, selectedValue) {
  const options = [el("option", { value: ALL_METRICS }, "All metrics")];
  for (const group of metricGroups(metrics)) {
    options.push(el(
      "optgroup",
      { label: group.label },
      el("option", { value: GROUP_PREFIX + group.group }, `All · ${group.label}`),
      ...group.metrics.map((m) => el("option", { value: m.key }, m.title)),
    ));
  }
  select.replaceChildren(...options);
  const values = [...select.options].map((o) => o.value);
  select.value = values.includes(String(selectedValue)) ? selectedValue : ALL_METRICS;
}

// The metrics a picker choice stands for.
function metricsFor(choice, metrics) {
  if (choice === ALL_METRICS) return metrics;
  if (choice.startsWith(GROUP_PREFIX)) return metrics.filter((m) => m.ui_category === choice.slice(GROUP_PREFIX.length));
  return metrics.filter((m) => m.key === choice);
}

function choiceLabel(choice, metrics) {
  if (choice === ALL_METRICS) return "all metrics";
  if (choice.startsWith(GROUP_PREFIX)) {
    const group = metrics.find((m) => m.ui_category === choice.slice(GROUP_PREFIX.length));
    return group ? group.ui_category_label : choice;
  }
  const metric = metrics.find((m) => m.key === choice);
  return metric ? metric.title : choice;
}

// What a metric's judge reads, in the words the dashboard uses (DeepEval field -> label).
const FIELD_LABELS = {
  input: "Question",
  actual_output: "Actual result",
  expected_output: "Expected result",
  context: "Context (known facts)",
  retrieval_context: "Retrieved context",
  turns: "Conversation",
  scenario: "Scenario",
  expected_outcome: "Expected outcome",
};

function fieldLabel(field) {
  return FIELD_LABELS[field] || field;
}

// "≥" for higher-is-better metrics, "≤" for violation rates (lower is better).
function comparator(direction) {
  return direction === "lower" ? "≤" : "≥";
}

// The catalog with each metric's threshold replaced by the user's (for charts).
function withUserThresholds(metrics, thresholds) {
  return metrics.map((m) => ({ ...m, threshold: thresholdFor(m, thresholds) }));
}

function thresholdFor(metric, thresholds) {
  const value = thresholds && thresholds[metric.key];
  return typeof value === "number" ? value : metric.threshold;
}

// The selected chatbot, the selected metric and the latest run are shared by
// the side panel and the dashboard.
const settings = {
  async get(key, fallback) {
    const data = await chrome.storage.local.get(key);
    return data[key] ?? fallback;
  },
  set(key, value) {
    return chrome.storage.local.set({ [key]: value });
  },
  onChange(callback) {
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area === "local") callback(changes);
    });
  },
};

async function fetchTargets() {
  return api("/api/targets");
}

// control (optional) is how a Stop button reaches a run in flight: pass the
// same object to stopRun(). A stopped metric comes back with status "cancelled"
// and the metrics after it are skipped.
async function runMetrics(target, metricKeys, onProgress, control = {}) {
  // Metrics run one after another; each is a full pass over the golden set.
  const results = [];
  const thresholds = await settings.get("thresholds", {});
  // Set in the side panel; both views honour it.
  const checkConsistency = await settings.get("checkConsistency", false);
  // Cases per run: only the dashboard sets it (control.limit); none = every case.
  const limit = control.limit || null;
  for (const [index, key] of metricKeys.entries()) {
    // The user's threshold for this metric; none set = the catalog default.
    const threshold = typeof thresholds[key] === "number" ? thresholds[key] : undefined;
    if (control.stopped) break;
    const step = { metricIndex: index, metricCount: metricKeys.length, startedAt: Date.now() };
    onProgress(key, null, step);
    // A run takes one chatbot call and several judge calls per golden, so
    // while it works, poll where it is and pass that on.
    const runId = `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    control.runId = runId;
    let finished = false;
    const poll = setInterval(async () => {
      try {
        const progress = await api(`/api/run/progress?run_id=${encodeURIComponent(runId)}`);
        // A poll that answers after the run ended must not paint over the result.
        if (progress.active && !finished) onProgress(key, null, { ...step, ...progress });
      } catch {
        // A missed poll only delays the next update.
      }
    }, 1000);
    let result;
    try {
      result = await api("/api/run", {
        method: "POST",
        body: {
          target_id: target.id, metric_key: key, threshold, run_id: runId, limit: limit || undefined,
          check_consistency: checkConsistency,
        },
      });
    } catch (error) {
      result = { key, status: "error", error: error.message, score: null, rows: [] };
    } finally {
      finished = true;
      clearInterval(poll);
    }
    results.push(result);
    onProgress(key, result);
    // A stopped run is partial: keep the last complete result on the charts.
    if (result.status === "cancelled") break;
    await settings.set("lastRun", {
      targetId: target.id,
      metricKey: key,
      finishedAt: Date.now(),
      result,
    });
    // lastRun only remembers the single most recent metric; caseDetails keeps
    // one entry per metric per target, so every card can show which question
    // and answer its own score came from, not just whichever ran last.
    await saveCaseDetails(target.id, key, result);
  }
  return results;
}

async function stopRun(control) {
  control.stopped = true;
  if (control.runId) {
    await api("/api/run/cancel", { method: "POST", body: { run_id: control.runId } });
  }
}

// What the dashboard's Details panel shows for one metric's last run.
function caseDetailsOf(result) {
  return {
    finishedAt: Date.now(),
    status: result.status,
    error: result.error || null,
    rows: result.rows || [],
    threshold: result.threshold,
    direction: result.direction,
    note: result.note || null,
  };
}

async function saveCaseDetails(targetId, metricKey, result) {
  const all = await settings.get("caseDetails", {});
  const forTarget = all[targetId] || {};
  forTarget[metricKey] = caseDetailsOf(result);
  await settings.set("caseDetails", { ...all, [targetId]: forTarget });
}

async function loadCaseDetails(targetId, metricKey) {
  const all = await settings.get("caseDetails", {});
  return (all[targetId] && all[targetId][metricKey]) || null;
}

async function clearCaseDetails(targetId) {
  const all = await settings.get("caseDetails", {});
  if (!(targetId in all)) return;
  const { [targetId]: _dropped, ...rest } = all;
  await settings.set("caseDetails", rest);
}
