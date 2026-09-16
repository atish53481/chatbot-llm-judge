// Chart builders shared by the side panel and the dashboard (Chart.js 4).
// Every metric scores 0..1, higher is better, and its threshold is a minimum.
// Colors come from lib/theme.css at draw time, so light and dark mode swap in
// one place. The series palette passed the dataviz palette validator; in light
// mode aqua, yellow and magenta sit below 3:1 on the surface, so every chart
// keeps a legend and a table view.

Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", sans-serif';
Chart.defaults.font.size = 12;

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function withAlpha(hex, alpha) {
  const value = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(value.slice(i, i + 2), 16));
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function metricColor(metricKey, catalog) {
  // A metric keeps its color everywhere: the color follows its catalog
  // position, never its position in a filtered list.
  const index = Math.max(0, catalog.findIndex((m) => m.key === metricKey));
  return cssVar(`--series-${(index % 7) + 1}`);
}

function chartInk() {
  return {
    surface: cssVar("--surface-1"),
    raised: cssVar("--surface-raised"),
    primary: cssVar("--text-primary"),
    secondary: cssVar("--text-secondary"),
    muted: cssVar("--text-muted"),
    grid: cssVar("--grid"),
    axis: cssVar("--axis"),
    border: cssVar("--border"),
  };
}

function sharedOptions(ink, legendKeyHeight) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    color: ink.secondary,
    plugins: {
      legend: {
        position: "top",
        align: "start",
        labels: { color: ink.secondary, boxWidth: 14, boxHeight: legendKeyHeight },
      },
      tooltip: {
        backgroundColor: ink.raised,
        titleColor: ink.secondary,
        bodyColor: ink.primary,
        borderColor: ink.border,
        borderWidth: 1,
        padding: 8,
        boxWidth: 12,
        boxHeight: 2,
      },
    },
  };
}

// Value + pass/fail at each bar tip: text ink for the words, the status color
// only on the ✓/✕ icon.
const scoreLabels = {
  id: "scoreLabels",
  afterDatasetsDraw(chart, _args, options) {
    const rows = options.rows;
    const meta = chart.getDatasetMeta(0);
    if (!rows || meta.hidden) return;
    const { ctx } = chart;
    ctx.save();
    ctx.font = `12px ${Chart.defaults.font.family}`;
    ctx.textBaseline = "middle";
    meta.data.forEach((bar, i) => {
      const row = rows[i];
      if (!row) return;
      const x = bar.x + 6;
      ctx.fillStyle = row.passed ? options.goodColor : options.criticalColor;
      ctx.fillText(row.passed ? "✓" : "✕", x, bar.y);
      ctx.fillStyle = options.textColor;
      ctx.fillText(`${formatScore(row.score)} ${row.passed ? "pass" : "fail"}`, x + 14, bar.y);
    });
    ctx.restore();
  },
};

// Vertical hairline at the hovered run on the trend chart.
const crosshair = {
  id: "crosshair",
  afterDatasetsDraw(chart, _args, options) {
    const active = chart.tooltip ? chart.tooltip.getActiveElements() : [];
    if (!active.length) return;
    const x = active[0].element.x;
    const { top, bottom } = chart.chartArea;
    const { ctx } = chart;
    ctx.save();
    ctx.strokeStyle = options.color;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, top);
    ctx.lineTo(x, bottom);
    ctx.stroke();
    ctx.restore();
  },
};

// rows: /api/runs/latest rows ({metric_key, score, passed, ts});
// catalog: /api/metrics; kind: "bar" | "radar".
function latestChartConfig(rows, catalog, kind) {
  const ink = chartInk();
  const byKey = new Map(catalog.map((m) => [m.key, m]));
  // Catalog order keeps each metric in the same place from run to run.
  const ordered = catalog
    .map((m) => rows.find((r) => r.metric_key === m.key))
    .filter(Boolean);
  const labels = ordered.map((r) => byKey.get(r.metric_key).title);
  const scores = ordered.map((r) => r.score);
  const thresholds = ordered.map((r) => byKey.get(r.metric_key).threshold);
  const accent = cssVar("--series-1");
  const threshold = {
    label: "Threshold (minimum)",
    data: thresholds,
    borderColor: ink.secondary,
    borderWidth: 1,
    borderDash: [4, 4],
    pointRadius: 0,
    pointHitRadius: 12,
    fill: false,
  };
  const tooltipLabel = (item) => {
    if (item.datasetIndex === 1) return ` minimum ${formatScore(item.raw)}`;
    const row = ordered[item.dataIndex];
    return ` ${formatScore(item.raw)} · ${row.passed ? "pass" : "fail"} · ${formatRunTime(row.ts)}`;
  };

  if (kind === "radar") {
    const options = sharedOptions(ink, 2);
    options.plugins.tooltip.callbacks = { label: tooltipLabel };
    options.scales = {
      r: {
        min: 0,
        max: 1,
        ticks: { stepSize: 0.2, color: ink.muted, backdropColor: "transparent" },
        grid: { color: ink.grid },
        angleLines: { color: ink.grid },
        pointLabels: { color: ink.secondary },
      },
    };
    return {
      type: "radar",
      data: {
        labels,
        datasets: [
          {
            label: "Score",
            data: scores,
            borderColor: accent,
            backgroundColor: withAlpha(accent, 0.1),
            borderWidth: 2,
            fill: true,
            pointRadius: 4,
            pointHoverRadius: 6,
            pointHitRadius: 12,
            pointBackgroundColor: accent,
            pointBorderColor: ink.surface,
            pointBorderWidth: 2,
          },
          threshold,
        ],
      },
      options,
    };
  }

  const options = sharedOptions(ink, 8);
  options.indexAxis = "y";
  options.layout = { padding: { right: 84 } };
  options.plugins.tooltip.callbacks = { label: tooltipLabel };
  options.plugins.scoreLabels = {
    rows: ordered,
    textColor: ink.secondary,
    goodColor: cssVar("--status-good"),
    criticalColor: cssVar("--status-critical"),
  };
  options.scales = {
    x: {
      min: 0,
      max: 1,
      ticks: { stepSize: 0.2, color: ink.muted },
      grid: { color: ink.grid },
      border: { color: ink.axis },
    },
    y: {
      ticks: { color: ink.secondary },
      grid: { display: false },
      border: { color: ink.axis },
    },
  };
  return {
    type: "bar",
    data: {
      labels,
      datasets: [
        {
          label: "Score",
          data: scores,
          backgroundColor: accent,
          hoverBackgroundColor: cssVar("--series-1-hover"),
          maxBarThickness: 24,
          borderSkipped: false,
          borderRadius: { topLeft: 0, bottomLeft: 0, topRight: 4, bottomRight: 4 },
        },
        { ...threshold, type: "line", indexAxis: "y" },
      ],
    },
    options,
    plugins: [scoreLabels],
  };
}

// historyByMetric: { metricKey: /api/history rows } for the metrics to show;
// catalog: /api/metrics (for stable colors and thresholds).
function trendChartConfig(historyByMetric, catalog) {
  const ink = chartInk();
  const shown = catalog.filter((m) => (historyByMetric[m.key] || []).length > 0);
  const times = [...new Set(shown.flatMap((m) => historyByMetric[m.key].map((r) => r.ts)))].sort();
  const single = shown.length === 1;
  const datasets = shown.map((m) => {
    const color = metricColor(m.key, catalog);
    const scoreAt = new Map(historyByMetric[m.key].map((r) => [r.ts, r.score]));
    return {
      label: m.title,
      data: times.map((ts) => (scoreAt.has(ts) ? scoreAt.get(ts) : null)),
      borderColor: color,
      backgroundColor: single ? withAlpha(color, 0.1) : color,
      fill: single ? "origin" : false,
      borderWidth: 2,
      borderCapStyle: "round",
      borderJoinStyle: "round",
      spanGaps: true,
      pointRadius: 4,
      pointHoverRadius: 6,
      pointHitRadius: 12,
      pointBackgroundColor: color,
      pointBorderColor: ink.surface,
      pointBorderWidth: 2,
    };
  });
  if (single) {
    datasets.push({
      label: "Threshold (minimum)",
      data: times.map(() => shown[0].threshold),
      borderColor: ink.secondary,
      borderWidth: 1,
      borderDash: [4, 4],
      pointRadius: 0,
      pointHitRadius: 0,
      fill: false,
    });
  }
  const options = sharedOptions(ink, 2);
  options.interaction = { mode: "index", intersect: false };
  options.plugins.tooltip.callbacks = {
    label: (item) => ` ${formatScore(item.raw)}  ${item.dataset.label}`,
  };
  options.plugins.crosshair = { color: ink.axis };
  options.scales = {
    x: {
      ticks: { color: ink.muted, maxRotation: 0, autoSkip: true },
      grid: { display: false },
      border: { color: ink.axis },
    },
    y: {
      min: 0,
      max: 1,
      ticks: { stepSize: 0.2, color: ink.muted },
      grid: { color: ink.grid },
      border: { color: ink.axis },
    },
  };
  return {
    type: "line",
    data: { labels: times.map(formatRunTime), datasets },
    options,
    plugins: [crosshair],
  };
}

function drawChart(canvas, previous, config) {
  if (previous) previous.destroy();
  return new Chart(canvas, config);
}
