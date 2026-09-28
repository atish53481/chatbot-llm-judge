// Printable evaluation report for one chatbot: the dashboard opens this page
// with ?target=<id>, it renders every metric's latest run from the backend and
// the case details the extension saved, then opens the print dialog so the
// user can choose "Save as PDF". Nothing here changes any data.

const $ = (id) => document.getElementById(id);

function normalized(metric, score) {
  if (typeof score !== "number") return null;
  return metric.direction === "lower" ? 1 - score : score;
}

// Only the origin and path: a captured URL's query string can carry tokens.
function safeUrl(url) {
  try {
    const u = new URL(url);
    return `${u.origin}${u.pathname}`;
  } catch {
    return "";
  }
}

function dl(pairs) {
  return el("dl", { className: "facts" }, ...pairs.flatMap(([label, value]) => [
    el("dt", {}, label),
    el("dd", {}, value),
  ]));
}

function resultCell(run) {
  if (!run) return el("span", { className: "result pending" }, "not run");
  return el("span", { className: `result ${run.passed ? "pass" : "fail"}` }, run.passed ? "pass" : "fail");
}

function cover(target, judge, metrics, latestByKey) {
  const run = metrics.filter((m) => latestByKey[m.key]);
  const scores = run.map((m) => normalized(m, latestByKey[m.key].score)).filter((s) => s !== null);
  const average = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null;
  const passed = run.filter((m) => latestByKey[m.key].passed).length;
  return el(
    "section",
    { className: "cover" },
    el("h1", {}, "Chatbot evaluation report"),
    el("p", { className: "subtitle" }, target.name),
    dl([
      ["Chatbot URL", safeUrl(target.config.url) || "–"],
      ["Golden set", themeOf(target)],
      ["Judge", judge ? `${judge.model} (${safeUrl(judge.base_url) || judge.base_url})` : "–"],
      ["Generated", new Date().toLocaleString()],
    ]),
    el(
      "div",
      { className: "headline" },
      el("div", {}, el("b", {}, average === null ? "–" : formatScore(average)), el("small", {}, "average score")),
      el("div", {}, el("b", { className: "pass" }, String(passed)), el("small", {}, "passed")),
      el("div", {}, el("b", { className: "fail" }, String(run.length - passed)), el("small", {}, "failed")),
      el("div", {}, el("b", {}, String(metrics.length - run.length)), el("small", {}, "not run")),
    ),
    el("p", { className: "muted small" }, "Average score: each metric's latest run on a higher-is-better scale (lower-is-better metrics counted as 1 − score)."),
  );
}

function summaryTable(metrics, latestByKey, thresholds) {
  return el(
    "section",
    {},
    el("h2", {}, "Summary"),
    el(
      "table",
      { className: "summary" },
      el("thead", {}, el("tr", {}, ...["Metric", "Category", "Score", "Threshold", "Result", "Cases", "Judge tokens", "Last run"]
        .map((h) => el("th", {}, h)))),
      el("tbody", {}, ...metrics.map((m) => {
        const run = latestByKey[m.key];
        const threshold = thresholdFor(m, thresholds);
        return el(
          "tr",
          {},
          el("td", {}, m.title),
          el("td", {}, m.ui_category_label || m.ui_category || ""),
          el("td", { className: "num" }, run ? formatScore(run.score) : "–"),
          el("td", { className: "num" }, `${comparator(m.direction)} ${formatScore(threshold)}`),
          el("td", {}, resultCell(run)),
          el("td", { className: "num" }, run && run.cases_run != null
            ? `${run.cases_run}${run.cases_skipped ? ` (+${run.cases_skipped} skipped)` : ""}` : "–"),
          el("td", { className: "num" }, run && typeof run.judge_tokens === "number" ? formatTokens(run.judge_tokens) : "–"),
          el("td", {}, run ? formatRunTime(run.ts) : "–"),
        );
      })),
    ),
  );
}

// Details saved by this browser belong to this run only if not older than it.
function detailsFor(run, details) {
  return details && run && details.status !== "error" && details.finishedAt >= Date.parse(run.ts) - 60_000
    ? details : null;
}

// Failed metrics, furthest from passing first, each with the judge's reasons
// and what to change in the chatbot or check in the test.
function actionPlan(metrics, latestByKey, detailsByKey, thresholds) {
  const failed = metrics
    .filter((m) => latestByKey[m.key] && !latestByKey[m.key].passed)
    .map((m) => {
      const run = latestByKey[m.key];
      const threshold = thresholdFor(m, thresholds);
      const shortfall = m.direction === "lower" ? run.score - threshold : threshold - run.score;
      return { metric: m, run, threshold, shortfall };
    })
    .sort((a, b) => b.shortfall - a.shortfall);
  if (!failed.length) {
    return el("section", {}, el("h2", {}, "Action plan"), el("p", {}, "Every metric that ran passed its threshold."));
  }
  return el(
    "section",
    { className: "action-plan" },
    el("h2", {}, `Action plan: ${failed.length} failed metric${failed.length === 1 ? "" : "s"}`),
    el("p", { className: "muted small" }, "Furthest from passing first. A failing score does not always mean the chatbot is wrong: check the test too."),
    ...failed.map(({ metric, run, threshold }) => {
      const details = detailsFor(run, detailsByKey[metric.key]);
      const failedCases = details ? details.rows.filter((r) => !r.passed).length : null;
      return el(
        "div",
        { className: "plan-item" },
        el("h3", {}, metric.title, " ", resultCell(run)),
        el("p", {}, gapText(metric.direction, run.score, threshold),
          failedCases !== null ? ` · ${failedCases} of ${details.rows.length} cases failed` : ""),
        improvementBlock(metric, details),
      );
    }),
  );
}

function caseBlock(metric, row, direction, threshold) {
  const pairs = [["Question", row.input || row.question || ""], ["Actual result", row.actual_output || ""]];
  if (row.expected_output) pairs.push(["Expected result", row.expected_output]);
  if (row.context && row.context.length) pairs.push(["Context", el("ul", {}, ...row.context.map((c) => el("li", {}, c)))]);
  if (row.scores && row.scores.length === 2) {
    pairs.push(["Two scorings", `${formatScore(row.scores[0])} and ${formatScore(row.scores[1])} (averaged)`]);
  }
  pairs.push(["Why", row.reason || "No reason given."]);
  return el(
    "div",
    { className: "case" },
    el("p", { className: "case-head fail" }, `✕ fail · ${formatScore(row.score)} ${comparator(direction)} ${formatScore(threshold)}`),
    dl(pairs),
  );
}

function metricSection(metric, run, details, thresholds) {
  const threshold = details && typeof details.threshold === "number" ? details.threshold : thresholdFor(metric, thresholds);
  const direction = (details && details.direction) || metric.direction;
  const parts = [
    el("h3", {}, metric.title, " ", resultCell(run)),
    dl(metricMeaning(metric)),
  ];
  // Details saved by this browser match this run only if they are not older than it.
  const sameRun = details && run && details.finishedAt >= Date.parse(run.ts) - 60_000;
  if (!sameRun || details.status === "error") {
    parts.push(dl([
      ["Latest run", `${formatScore(run.score)} ${comparator(direction)} ${formatScore(threshold)} · ${formatRunTime(run.ts)}`],
      ["Cases", run.cases_run != null ? String(run.cases_run) : "–"],
      ["Judge", run.judge_model || "–"],
    ]));
    parts.push(el("p", { className: "muted small" }, "Case-by-case details were not saved in this browser for this run; run the metric from the dashboard or side panel to include them."));
    return el("section", { className: "metric" }, ...parts);
  }
  const verdict = runVerdict(details);
  if (verdict) parts.push(el("p", { className: `verdict ${details.status}` }, verdict));
  const facts = runFacts(details);
  if (facts.length) parts.push(dl(facts));
  if (details.note) parts.push(el("p", { className: "note" }, `⚠ ${details.note}`));
  const failed = details.rows.filter((r) => !r.passed);
  const passedCount = details.rows.length - failed.length;
  if (failed.length) {
    parts.push(el("h4", {}, `Failed cases (${failed.length})`));
    parts.push(...failed.map((row) => caseBlock(metric, row, direction, threshold)));
  }
  parts.push(el("p", { className: "muted small" }, passedCount
    ? `${passedCount} passed case${passedCount === 1 ? "" : "s"} not listed.`
    : "No cases passed."));
  return el("section", { className: "metric" }, ...parts);
}

function appendix() {
  return el(
    "section",
    { className: "appendix" },
    el("h2", {}, "How to read the scores"),
    el(
      "ul",
      {},
      el("li", {}, "Each metric sends the golden questions to the chatbot and has the judge model score every reply from 0 to 1."),
      el("li", {}, "A run's score is the average of its case scores; the run passes when the average meets the threshold."),
      el("li", {}, "Higher is better (≥) for most metrics. Hallucination, Bias, Toxicity and PII Leakage are violation rates: lower is better (≤), shown as 1 − DeepEval's score."),
      el("li", {}, "Skipped cases were longer than the chatbot's max message length and were not sent."),
      el("li", {}, "With the judge-consistency check on, every reply is scored twice; a run whose two scorings of one case differ by more than 0.15 is marked judge unstable."),
      el("li", {}, "Replies such as \"limit reached\" or \"please log in\" stop a run as an error; such runs are not scored or listed."),
    ),
  );
}

async function build() {
  const targetId = Number(new URLSearchParams(location.search).get("target"));
  const [targets, judge, thresholds] = await Promise.all([
    fetchTargets(),
    api("/api/judge/settings").catch(() => null),
    settings.get("thresholds", {}),
  ]);
  const target = targets.find((t) => t.id === targetId);
  if (!target) throw new Error("Chatbot not found: open the report from the dashboard.");
  const [metrics, latest] = await Promise.all([
    api(`/api/metrics?target_id=${target.id}`),
    api(`/api/runs/latest?target_id=${target.id}`),
  ]);
  const latestByKey = Object.fromEntries(latest.map((r) => [r.metric_key, r]));
  const details = Object.fromEntries(await Promise.all(
    metrics.map(async (m) => [m.key, await loadCaseDetails(target.id, m.key)]),
  ));
  document.title = `Evaluation report · ${target.name}`;
  const ranMetrics = metrics.filter((m) => latestByKey[m.key]);
  $("report").replaceChildren(
    cover(target, judge, metrics, latestByKey),
    summaryTable(metrics, latestByKey, thresholds),
    actionPlan(metrics, latestByKey, details, thresholds),
    el("h2", { className: "page-break" }, "Metric details"),
    ...ranMetrics.map((m) => metricSection(m, latestByKey[m.key], details[m.key], thresholds)),
    appendix(),
  );
  return ranMetrics.length;
}

$("print-button").addEventListener("click", () => window.print());

build()
  .then((ran) => {
    $("report-status").textContent = ran
      ? "Report ready. Choose \"Save as PDF\" as the printer."
      : "No metric has been run for this chatbot yet: the report has only the summary.";
    $("print-button").disabled = false;
    if (ran) setTimeout(() => window.print(), 300);
  })
  .catch((error) => {
    $("report-status").textContent = error.message;
  });
