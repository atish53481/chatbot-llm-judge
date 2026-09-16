// Helpers shared by the side panel and the dashboard. Everything that comes
// from a chatbot or the backend is inserted as text, never as HTML.

const ALL_METRICS = "__all__";
const DEFAULT_THEME = "general_support";

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
  const kinds = { mock: "sample", http: "API", dom: "web page" };
  return `${target.name} (${kinds[target.type] || target.type})`;
}

function metricOptions(metrics) {
  return [
    { value: ALL_METRICS, label: "All metrics" },
    ...metrics.map((m) => ({ value: m.key, label: m.title })),
  ];
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
  const targets = await api("/api/targets");
  if (targets.length > 0) return targets;
  // First run: add the sample chatbot so everything works with zero setup.
  const sample = await api("/api/targets", {
    method: "POST",
    body: { name: "Sample chatbot", type: "mock", config: { theme: DEFAULT_THEME } },
  });
  return [sample];
}

async function runMetrics(target, metricKeys, onProgress) {
  // Metrics run one after another; each is a full pass over the golden set.
  const results = [];
  for (const key of metricKeys) {
    onProgress(key, null);
    let result;
    try {
      result = await api("/api/run", {
        method: "POST",
        body: { target_id: target.id, metric_key: key },
      });
    } catch (error) {
      result = { key, status: "error", error: error.message, score: null, rows: [] };
    }
    results.push(result);
    onProgress(key, result);
    await settings.set("lastRun", {
      targetId: target.id,
      metricKey: key,
      finishedAt: Date.now(),
      result,
    });
  }
  return results;
}
