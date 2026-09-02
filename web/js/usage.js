// Usage tab: Claude Code + Codex transcript spend, laid out like T3 Code's
// Usage page and painted with this console's tokens.

import { api } from "./api.js";
import { MARK, PROVIDER_LABEL, PROVIDER_ORDER } from "./brands.js";
import { $, clear, el } from "./dom.js";
import { store, update } from "./store.js";

const WINDOWS = [
  { days: 1, label: "Past 24h" },
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
];

const PROVIDER_COLOR = {
  claude: "--usage-claude",
  codex: "--usage-codex",
  grok: "--usage-grok",
  cursor: "--usage-cursor",
};
const PROVIDER_FALLBACK = {
  claude: "#d97757",
  codex: "#d4d4d4",
  grok: "#9aa4b2",
  cursor: "#4caf7a",
};

const VIEW_WIDTH = 960;
const VIEW_HEIGHT = 260;
const TICK_COUNT = 4;
const PLOT_TOP = 8;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const USD = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
const INTEGER = new Intl.NumberFormat("en-US");

let painted = null;

export function createUsageController({ request, publish = () => {}, render = () => {} }) {
  let state = {
    days: 30,
    metric: "cost",
    breakdown: "model",
    loading: false,
    error: null,
  };
  let requestId = 0;
  let projected = Object.freeze({ ...state });

  const view = () => projected;
  const change = (patch) => {
    state = { ...state, ...patch };
    projected = Object.freeze({ ...state });
    render(projected);
  };

  async function refresh() {
    const currentRequest = ++requestId;
    const days = state.days;
    change({ loading: true, error: null });
    try {
      const usage = await request(days);
      if (currentRequest !== requestId) return false;
      change({ loading: false, error: null });
      publish(usage);
      return true;
    } catch (error) {
      if (currentRequest !== requestId) return false;
      change({ loading: false, error: error.message || "Usage request failed" });
      return false;
    }
  }

  return {
    view,
    refresh,
    selectDays(value) {
      const days = Number(value);
      if (!Number.isInteger(days) || days <= 0) return Promise.resolve(false);
      if (days !== state.days) {
        change({ days });
        publish(null);
      }
      return refresh();
    },
    selectMetric(metric) {
      if (!["cost", "tokens"].includes(metric) || metric === state.metric) return false;
      change({ metric });
      return true;
    },
    selectBreakdown(breakdown) {
      if (!["model", "time"].includes(breakdown) || breakdown === state.breakdown) return false;
      change({ breakdown });
      return true;
    },
  };
}

const controller = createUsageController({
  request: (days) => api.usage(days),
  publish(usage) {
    update(usage === null ? { usage: null } : { usage, usageSettings: usage.settings });
  },
  render() {
    painted = null;
    paint(store);
  },
});

// ------------------------------------------------------------------ format

function formatUsd(value) {
  return USD.format(num(value));
}

function formatCount(value) {
  return INTEGER.format(Math.round(num(value)));
}

function formatTokens(value) {
  const n = num(value);
  const abs = Math.abs(n);
  if (abs >= 1e12) return `${trim(n / 1e12)}T`;
  if (abs >= 1e9) return `${trim(n / 1e9)}B`;
  if (abs >= 1e6) return `${trim(n / 1e6)}M`;
  if (abs >= 1e3) return `${trim(n / 1e3)}K`;
  return INTEGER.format(Math.round(n));
}

function trim(value) {
  const abs = Math.abs(value);
  const digits = abs >= 100 ? 0 : abs >= 10 ? 1 : 2;
  return value.toFixed(digits).replace(/\.0+$/, "");
}

function formatPercent(share, digits = 1) {
  return `${(num(share) * 100).toFixed(digits)}%`;
}

function formatDayShort(day) {
  const parts = (day || "").split("-").map(Number);
  if (parts.length < 3 || parts.some((part) => !Number.isFinite(part))) return day || "";
  return `${MONTHS[parts[1] - 1] || ""} ${parts[2]}`;
}

function formatHourShort(hourStart, timeZone) {
  const instant = new Date(hourStart);
  if (Number.isNaN(instant.getTime())) return hourStart || "";
  return new Intl.DateTimeFormat("en-US", { timeZone, hour: "numeric" }).format(instant);
}

function formatDateTimeShort(instant, timeZone) {
  const date = new Date(instant);
  if (Number.isNaN(date.getTime())) return instant || "";
  return new Intl.DateTimeFormat("en-US", {
    timeZone,
    month: "short",
    day: "numeric",
    hour: "numeric",
  }).format(date);
}

function enumerateDays(sinceDay, untilDay) {
  const days = [];
  const start = Date.parse(`${sinceDay}T00:00:00Z`);
  const end = Date.parse(`${untilDay}T00:00:00Z`);
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return days;
  for (let cursor = start; cursor <= end; cursor += 86_400_000) {
    days.push(new Date(cursor).toISOString().slice(0, 10));
  }
  return days;
}

function enumerateHourStarts(sinceTime, untilTime) {
  const starts = [];
  const startMs = Date.parse(sinceTime);
  const end = Date.parse(untilTime);
  if (Number.isNaN(startMs) || Number.isNaN(end) || end <= startMs) return starts;
  let cursor = Math.floor(startMs / 3_600_000) * 3_600_000;
  for (; cursor < end; cursor += 3_600_000) {
    starts.push(new Date(cursor).toISOString());
  }
  return starts;
}

function num(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

// ------------------------------------------------------------------ chart

function niceScale(peak, count) {
  if (peak <= 0) return { max: 0, ticks: [0] };
  const rawStep = peak / count;
  const magnitude = 10 ** Math.floor(Math.log10(rawStep));
  const normalized = rawStep / magnitude;
  const step = (normalized > 5 ? 10 : normalized > 2 ? 5 : normalized > 1 ? 2 : 1) * magnitude;
  const max = Math.ceil(peak / step) * step;
  const ticks = [];
  for (let value = 0; value <= max + step * 1e-6; value += step) ticks.push(value);
  return { max, ticks };
}

function monotoneTangents(points) {
  const count = points.length;
  if (count < 2) return [0];
  const slopes = [];
  for (let index = 0; index < count - 1; index += 1) {
    const dx = points[index + 1].x - points[index].x;
    const dy = points[index + 1].y - points[index].y;
    slopes.push(dx === 0 ? 0 : dy / dx);
  }
  const tangents = Array.from({ length: count }, () => 0);
  tangents[0] = slopes[0] || 0;
  tangents[count - 1] = slopes[count - 2] || 0;
  for (let index = 1; index < count - 1; index += 1) {
    const previous = slopes[index - 1] || 0;
    const next = slopes[index] || 0;
    tangents[index] = previous * next <= 0 ? 0 : (previous + next) / 2;
  }
  for (let index = 0; index < count - 1; index += 1) {
    const slope = slopes[index] || 0;
    if (slope === 0) {
      tangents[index] = 0;
      tangents[index + 1] = 0;
      continue;
    }
    const a = tangents[index] / slope;
    const b = tangents[index + 1] / slope;
    const magnitude = a * a + b * b;
    if (magnitude > 9) {
      const scale = 3 / Math.sqrt(magnitude);
      tangents[index] = scale * a * slope;
      tangents[index + 1] = scale * b * slope;
    }
  }
  return tangents;
}

function curvePath(points) {
  if (points.length < 2) return "";
  const tangents = monotoneTangents(points);
  let path = `M${points[0].x.toFixed(2)},${points[0].y.toFixed(2)}`;
  for (let index = 0; index < points.length - 1; index += 1) {
    const from = points[index];
    const to = points[index + 1];
    const dx = to.x - from.x;
    path += ` C${(from.x + dx / 3).toFixed(2)},${(from.y + (tangents[index] * dx) / 3).toFixed(2)} ${(to.x - dx / 3).toFixed(2)},${(to.y - (tangents[index + 1] * dx) / 3).toFixed(2)} ${to.x.toFixed(2)},${to.y.toFixed(2)}`;
  }
  return path;
}

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined) continue;
    node.setAttribute(key, String(value));
  }
  return node;
}

function colorFor(provider, root) {
  const name = PROVIDER_COLOR[provider] || "--usage-codex";
  return getComputedStyle(root).getPropertyValue(name).trim() || PROVIDER_FALLBACK[provider] || "#d4d4d4";
}

function buildChart(root, periods, byPeriod, timeZone, resolution, providerOrder, metric) {
  const order = providerOrder || PROVIDER_ORDER;
  const format = metric === "tokens" ? formatTokens : formatUsd;
  const wrap = el("div", { class: "usage-chart" });
  const axis = el("div", { class: "usage-chart-axis" });
  const plot = el("div", { class: "usage-chart-plot" });
  const svg = svgEl("svg", {
    viewBox: `0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`,
    preserveAspectRatio: "none",
    class: "usage-chart-svg",
  });
  const tooltip = el("div", { class: "usage-chart-tip", hidden: true });
  const xlabels = el("div", { class: "usage-chart-x" });
  plot.append(svg, tooltip);
  wrap.append(axis, plot, xlabels);

  if (!periods.length) {
    axis.append(el("span", { text: "0" }));
    xlabels.append(el("span", { text: "—" }));
    return wrap;
  }

  const columns = periods.map((period) => {
    const entry = byPeriod.get(period);
    const bands = order.map((provider) => {
      const row = entry?.bySource?.[provider];
      return { provider, value: metric === "tokens" ? row?.totalTokens || 0 : row?.costUsd || 0 };
    });
    return { bands, total: bands.reduce((sum, band) => sum + band.value, 0) };
  });

  const peak = columns.reduce((max, column) => column.bands.reduce((inner, band) => Math.max(inner, band.value), max), 0);
  const { max, ticks } = niceScale(peak, TICK_COUNT);
  const step = periods.length === 1 ? 0 : VIEW_WIDTH / (periods.length - 1);
  const toY = (value) => (max === 0 ? VIEW_HEIGHT : VIEW_HEIGHT - (value / max) * (VIEW_HEIGHT - PLOT_TOP));

  for (const tick of ticks) {
    axis.append(el("span", { text: tick === 0 ? "0" : format(tick) }));
    svg.append(
      svgEl("line", {
        x1: 0,
        x2: VIEW_WIDTH,
        y1: toY(tick).toFixed(2),
        y2: toY(tick).toFixed(2),
        class: "usage-grid",
      }),
    );
  }

  const series = order.map((provider, providerIndex) => {
    const line = curvePath(
      columns.map((column, dayIndex) => ({
        x: dayIndex * step,
        y: toY(column.bands[providerIndex]?.value || 0),
      })),
    );
    return {
      provider,
      total: columns.reduce((sum, column) => sum + (column.bands[providerIndex]?.value || 0), 0),
      line,
      area: line ? `${line} L${VIEW_WIDTH},${VIEW_HEIGHT} L0,${VIEW_HEIGHT} Z` : "",
    };
  }).sort((a, b) => b.total - a.total);

  for (const row of series) {
    if (!row.area) continue;
    svg.append(
      svgEl("path", {
        d: row.area,
        fill: colorFor(row.provider, root),
        "fill-opacity": "0.16",
        class: `usage-area usage-area-${row.provider}`,
      }),
    );
  }
  for (const row of series) {
    if (!row.line) continue;
    svg.append(
      svgEl("path", {
        d: row.line,
        fill: "none",
        stroke: colorFor(row.provider, root),
        "stroke-width": "2",
        "stroke-linejoin": "round",
        "vector-effect": "non-scaling-stroke",
        class: `usage-line usage-line-${row.provider}`,
      }),
    );
  }

  const hover = svgEl("line", { y1: 0, y2: VIEW_HEIGHT, class: "usage-hover", hidden: "" });
  svg.append(hover);

  const labelPeriod = (period) =>
    resolution === "hour" ? formatHourShort(period, timeZone) : formatDayShort(period);
  xlabels.append(
    el("span", { text: labelPeriod(periods[0]) }),
    el("span", { text: labelPeriod(periods[Math.floor(periods.length / 2)]) }),
    el("span", { text: labelPeriod(periods[periods.length - 1]) }),
  );

  const showHover = (index) => {
    if (index == null) {
      hover.setAttribute("hidden", "");
      tooltip.hidden = true;
      return;
    }
    const x = periods.length === 1 ? VIEW_WIDTH / 2 : index * step;
    hover.removeAttribute("hidden");
    hover.setAttribute("x1", x.toFixed(2));
    hover.setAttribute("x2", x.toFixed(2));
    const column = columns[index];
    const left = periods.length <= 1 ? 0 : (index / (periods.length - 1)) * 100;
    tooltip.hidden = false;
    tooltip.style.left = `${left}%`;
    tooltip.style.transform = left > 60 ? "translateX(-100%)" : "translateX(0)";
    clear(tooltip);
    tooltip.append(
      el("div", { class: "usage-tip-when", text: labelPeriod(periods[index]) }),
      ...order.map((provider) =>
        el("div", { class: "usage-tip-row" }, [
          el("span", { class: `usage-mark usage-mark-${provider}`, html: MARK[provider] }),
          el("span", { class: "usage-tip-name", text: PROVIDER_LABEL[provider] }),
          el("span", { class: "usage-tip-val", text: format(column.bands.find((band) => band.provider === provider)?.value || 0) }),
        ]),
      ),
      el("div", { class: "usage-tip-row usage-tip-total" }, [
        el("span", { text: "Total" }),
        el("span", { class: "usage-tip-val", text: format(column.total) }),
      ]),
    );
  };

  plot.addEventListener("mousemove", (event) => {
    const bounds = plot.getBoundingClientRect();
    if (!bounds.width || !periods.length) return;
    const fraction = (event.clientX - bounds.left) / bounds.width;
    const index = Math.round(fraction * (periods.length - 1));
    showHover(Math.min(periods.length - 1, Math.max(0, index)));
  });
  plot.addEventListener("mouseleave", () => showHover(null));

  return wrap;
}

// ------------------------------------------------------------------ render

function segmented(options, current, attr) {
  const labels = {
    usageDays: "Usage period",
    usageMetric: "Usage metric",
    usageBreakdown: "Usage breakdown",
  };
  return el(
    "div",
    { class: "segmented", role: "group", "aria-label": labels[attr] || "Options" },
    options.map((option) =>
      el("button", {
        class: `seg${option.value === current ? " active" : ""}`,
        type: "button",
        text: option.label,
        "aria-pressed": String(option.value === current),
        dataset: { [attr]: String(option.value) },
      }),
    ),
  );
}

function metricCard(label, value, detail) {
  return el("div", { class: "usage-metric" }, [
    el("div", { class: "usage-metric-label", text: label }),
    el("div", { class: "usage-metric-value", text: value }),
    el("div", { class: "usage-metric-detail", text: detail }),
  ]);
}

function windowLabel(summary) {
  if (summary.resolution === "hour" && summary.sinceTime && summary.untilTime) {
    return `${formatDateTimeShort(summary.sinceTime, summary.timeZone)} to ${formatDateTimeShort(summary.untilTime, summary.timeZone)}`;
  }
  return `${formatDayShort(summary.sinceDay)} to ${formatDayShort(summary.untilDay)}`;
}

function skeleton() {
  return el("div", { class: "usage-skel", role: "status", "aria-label": "Loading usage data" }, [
    el("div", { class: "usage-hero" }, [
      el("div", { class: "usage-hero-copy" }, [
        el("div", { class: "usage-kicker", text: "Raw token cost" }),
        el("div", { class: "usage-hero-value usage-skel-block usage-skel-lg" }),
        el("div", { class: "usage-hero-note usage-skel-block usage-skel-sm" }),
      ]),
      el("div", { class: "usage-providers" }, [
        el("div", { class: "usage-provider usage-skel-block usage-skel-row" }),
        el("div", { class: "usage-provider usage-skel-block usage-skel-row" }),
      ]),
    ]),
    el("p", { class: "usage-scan", text: "Scanning local transcripts and enabled usage sources…" }),
  ]);
}

function coverageStrip(summary) {
  const byMachine = new Map();
  for (const source of summary.sources || []) {
    const id = source.machine || "this machine";
    const row = byMachine.get(id) || { machine: id, ok: [], issues: [] };
    if (source.status === "ok") row.ok.push(source);
    else row.issues.push(source);
    byMachine.set(id, row);
  }
  if (!byMachine.size) return null;
  return el(
    "div",
    { class: "usage-coverage" },
    [...byMachine.values()].map((row) => {
      if (row.issues.length) {
        const messages = row.issues.map((source) => {
          const label = PROVIDER_LABEL[source.provider] || source.provider;
          return `${label}: ${source.message || "could not report usage"}`;
        });
        return el("div", { class: "usage-coverage-row is-bad" }, [
          el("span", { class: "usage-coverage-name", text: row.machine }),
          el("span", { text: messages.join(" · ") }),
        ]);
      }
      const bits = row.ok
        .filter((source) => source.provider !== "hub")
        .map((source) => `${PROVIDER_LABEL[source.provider] || source.provider} ${formatCount(source.scannedFiles)} files`);
      return el("div", { class: "usage-coverage-row is-ok" }, [
        el("span", { class: "usage-coverage-name", text: row.machine }),
        el("span", { text: bits.join(" · ") || "ok" }),
      ]);
    }),
  );
}

function paint(snapshot) {
  const root = $("#usage-root");
  if (!root) return;
  const view = controller.view();
  const key = [
    snapshot.tab,
    snapshot.usage,
    snapshot.usageSettings,
    view,
  ];
  if (painted && painted.every((item, index) => item === key[index])) return;
  if (snapshot.tab !== "usage") {
    painted = key;
    return;
  }

  try {
    paintUsagePage(root, snapshot, view);
    painted = key;
  } catch (error) {
    painted = null;
    clear(root);
    root.append(
      el("div", { class: "usage-error", role: "alert", text: `Usage failed to render: ${error.message || error}` }),
    );
  }
}

function paintUsagePage(root, snapshot, view) {
  clear(root);
  const summary = snapshot.usage;
  const hourly = summary?.resolution === "hour";
  const { breakdown, days, error, loading, metric } = view;

  const head = el("div", { class: "usage-head" }, [
    el("div", { class: "usage-head-copy" }, [
      el("h1", { class: "title", text: "Usage" }),
      el("p", {
        class: "usage-range",
        text: summary ? windowLabel(summary) : "Local coding-agent transcripts on this machine",
      }),
    ]),
    el("div", { class: "usage-head-actions" }, [
      segmented(
        WINDOWS.map((item) => ({ value: item.days, label: item.label })),
        days,
        "usageDays",
      ),
    ]),
  ]);
  root.append(head);

  if (error) {
    root.append(el("div", { class: "usage-error", role: "alert", text: error }));
    return;
  }
  if (loading || !summary) {
    root.append(skeleton());
    return;
  }

  const coverage = coverageStrip(summary);
  if (coverage) root.append(coverage);

  const report = summary.rollups;
  const merged = report.total;
  const sources = report.bySource;
  const models = report.byModel;
  const periods = new Map(report.periods.map((period) => [period.key, period]));
  const sourceOrder = sources.map((row) => row.source);
  const orderedSources = [...sources].sort((a, b) =>
    metric === "cost" ? b.costUsd - a.costUsd : b.totalTokens - a.totalTokens,
  );
  const periodKeys =
    hourly && summary.sinceTime && summary.untilTime
      ? enumerateHourStarts(summary.sinceTime, summary.untilTime)
      : enumerateDays(summary.sinceDay, summary.untilDay);
  const activePeriods = periodKeys.filter((keyName) => (periods.get(keyName)?.totalTokens || 0) > 0).length;
  const periodAverage = activePeriods === 0 ? 0 : merged.totalTokens / activePeriods;
  const observedInput = merged.uncachedInputTokens + merged.cachedInputTokens;
  const cachedShare = observedInput === 0 ? 0 : merged.cachedInputTokens / observedInput;
  const recent = [...periodKeys].reverse().slice(0, 8);

  const heroValue = metric === "cost" ? `${formatUsd(merged.costUsd)}*` : formatTokens(merged.totalTokens);
  const heroNote =
    metric === "cost"
      ? "* if billed at full API rate"
      : `Input, cache reads and output across ${formatCount(merged.sessions)} sessions.`;

  root.append(
    el("div", { class: "usage-hero" }, [
      el("div", { class: "usage-hero-copy" }, [
        el("div", { class: "usage-kicker", text: metric === "cost" ? "Raw token cost" : "Processed tokens" }),
        el("div", { class: "usage-hero-value", text: heroValue }),
        el("div", { class: "usage-hero-note", text: heroNote }),
      ]),
      el(
        "div",
        { class: "usage-providers" },
        orderedSources.length
          ? orderedSources.map((row) => {
              const share = metric === "cost" ? row.costShare : row.tokenShare;
              return el("div", { class: "usage-provider" }, [
                el("div", { class: "usage-provider-top" }, [
                  el("span", { class: `usage-mark usage-mark-${row.source}`, html: MARK[row.source] }),
                  el("span", { class: "usage-provider-name", text: PROVIDER_LABEL[row.source] || row.source }),
                  el("span", {
                    class: "usage-provider-val",
                    text: metric === "cost" ? formatUsd(row.costUsd) : formatTokens(row.totalTokens),
                  }),
                ]),
                el("div", { class: "usage-bar-track" }, [
                  el("div", {
                    class: `usage-bar-fill usage-bar-${row.source}`,
                    style: { width: `${Math.max(share * 100, share > 0 ? 1.5 : 0)}%` },
                  }),
                ]),
                el("div", {
                  class: "usage-provider-sub",
                  text:
                    metric === "cost"
                      ? `${formatPercent(share)} of cost · ${formatTokens(row.totalTokens)} tokens`
                      : `${formatPercent(share)} of tokens · ${formatUsd(row.costUsd)}`,
                }),
              ]);
            })
          : [el("div", { class: "usage-empty", text: "No activity in this window." })],
      ),
    ]),
  );

  const chartHead = el("div", { class: "sec-head usage-chart-head" }, [
    el("h2", {
      class: "sec-title",
      text: `${hourly ? "Hourly" : "Daily"} ${metric === "tokens" ? "processed tokens" : "cost"}`,
    }),
    el("span", { class: "spacer" }),
    el("div", { class: "usage-legend" }, [
      ...sourceOrder.map((source) =>
        el("span", { class: "usage-legend-item" }, [
          el("span", { class: `usage-mark usage-mark-${source}`, html: MARK[source] }),
          el("span", { text: PROVIDER_LABEL[source] }),
        ]),
      ),
    ]),
    segmented(
      [
        { value: "cost", label: "cost" },
        { value: "tokens", label: "tokens" },
      ],
      metric,
      "usageMetric",
    ),
  ]);
  root.append(
    el("section", { class: "sec usage-chart-sec" }, [
      chartHead,
      buildChart(root, periodKeys, periods, summary.timeZone, summary.resolution, sourceOrder, metric),
    ]),
  );

  const savingsDetail =
    merged.costUsd > 0 && merged.cacheSavingsUsd > merged.costUsd
      ? `${(merged.cacheSavingsUsd / merged.costUsd).toFixed(1)}x the raw token cost`
      : "vs full input rates";
  root.append(
    el("div", { class: "usage-metrics" }, [
      metricCard(
        "Processed tokens",
        formatTokens(merged.totalTokens),
        `${formatCount(merged.sessions)} sessions · ${formatTokens(periodAverage)} / ${hourly ? "hour" : "day"}`,
      ),
      metricCard("Cached input", formatTokens(merged.cachedInputTokens), `${formatPercent(cachedShare)} of observed input`),
      metricCard("Uncached input", formatTokens(merged.uncachedInputTokens), `${formatTokens(merged.cacheCreationTokens)} cache writes`),
      metricCard("Output", formatTokens(merged.outputTokens), `${formatTokens(merged.reasoningTokens)} reasoning`),
      metricCard("Cache savings", formatUsd(merged.cacheSavingsUsd), savingsDetail),
    ]),
  );

  const tableHead = el("div", { class: "sec-head" }, [
    el("h2", { class: "sec-title", text: "Breakdown" }),
    el("span", { class: "spacer" }),
    segmented(
      [
        { value: "model", label: "model" },
        { value: "time", label: hourly ? "hour" : "day" },
      ],
      breakdown,
      "usageBreakdown",
    ),
  ]);

  let table;
  if (breakdown === "model") {
    table = el("table", { class: "usage-table" }, [
      el("thead", {}, [
        el("tr", {}, [
          el("th", { text: "Model" }),
          el("th", { text: "Cost" }),
          el("th", { text: "Share" }),
          el("th", { text: "Tokens" }),
        ]),
      ]),
      el(
        "tbody",
        {},
        models.length
          ? models.map((row) =>
              el("tr", {}, [
                el("td", {}, [
                  el("div", { class: "usage-model" }, [
                    el("span", { class: `usage-mark usage-mark-${row.source}`, html: MARK[row.source] }),
                    el("span", { class: "mono", text: row.model }),
                  ]),
                ]),
                el("td", { class: "num", text: formatUsd(row.costUsd) }),
                el("td", { class: "num", text: formatPercent(row.costShare) }),
                el("td", { class: "num", text: formatTokens(row.totalTokens) }),
              ]),
            )
          : [el("tr", {}, [el("td", { colSpan: 4, class: "usage-empty", text: "No activity in this window." })])],
      ),
    ]);
  } else {
    table = el("table", { class: "usage-table" }, [
      el("thead", {}, [
        el("tr", {}, [
          el("th", { text: hourly ? "Hour" : "Day" }),
          ...sourceOrder.map((source) => el("th", { text: PROVIDER_LABEL[source] })),
          el("th", { text: "Total" }),
          el("th", { text: "Tokens" }),
        ]),
      ]),
      el(
        "tbody",
        {},
        recent.length
          ? recent.map((keyName) => {
              const period = periods.get(keyName);
              return el("tr", {}, [
                el("td", {
                  text: hourly ? formatHourShort(keyName, summary.timeZone) : formatDayShort(keyName),
                }),
                ...sourceOrder.map((source) =>
                  el("td", { class: "num", text: formatUsd(period?.bySource?.[source]?.costUsd || 0) }),
                ),
                el("td", { class: "num", text: formatUsd(period?.costUsd || 0) }),
                el("td", { class: "num", text: formatTokens(period?.totalTokens || 0) }),
              ]);
            })
          : [el("tr", {}, [el("td", { colSpan: 3 + sourceOrder.length, class: "usage-empty", text: "No activity in this window." })])],
      ),
    ]);
  }
  root.append(el("section", { class: "sec" }, [tableHead, table]));

  const notes = [];
  const unpricedTokens = models.reduce(
    (sum, row) => (row.unpricedRecords ? sum + row.totalTokens : sum),
    0,
  );
  if (unpricedTokens > 0) {
    notes.push(`${formatTokens(unpricedTokens)} tokens had no published API rate and were left unpriced.`);
  }
  if (summary.pricing?.status && summary.pricing.status !== "ok" && summary.pricing.status !== "fresh" && summary.pricing.status !== "cached") {
    notes.push(`Model rates: ${summary.pricing.status}.`);
  }
  for (const source of summary.sources || []) {
    const who = source.machine ? `${source.machine} ` : "";
    const label = PROVIDER_LABEL[source.provider] || source.provider;
    if (source.status === "failed") notes.push(`${who}${source.message || "could not report usage."}`);
    else if (source.status === "missing") notes.push(`${who}${label}: ${source.message || "no transcript directory."}`);
    else if (source.provider !== "hub") notes.push(`${who}${label}: ${formatCount(source.scannedFiles)} files, ${formatCount(source.sessions)} sessions.`);
  }
  notes.push(`Scanned in ${formatCount(summary.scanDurationMs)} ms.`);
  root.append(el("p", { class: "usage-foot", text: notes.join(" · ") }));
}

export function renderUsage(snapshot) {
  paint(snapshot);
}

export async function refreshUsage() {
  return controller.refresh();
}

export function isUsageLoading() {
  return controller.view().loading;
}

export function mountUsage() {
  const root = $("#usage-root");
  if (!root) return;
  root.addEventListener("click", (event) => {
    const daysBtn = event.target.closest("[data-usage-days]");
    if (daysBtn) {
      const days = Number(daysBtn.dataset.usageDays);
      controller.selectDays(days);
      return;
    }
    const metricBtn = event.target.closest("[data-usage-metric]");
    if (metricBtn) {
      controller.selectMetric(metricBtn.dataset.usageMetric);
      return;
    }
    const breakdownBtn = event.target.closest("[data-usage-breakdown]");
    if (breakdownBtn) {
      controller.selectBreakdown(breakdownBtn.dataset.usageBreakdown);
    }
  });
}
