// Usage tab: Claude Code + Codex transcript spend, laid out like T3 Code's
// Usage page and painted with this console's tokens.

import { api } from "./api.js";
import { MARK, PROVIDER_LABEL, PROVIDER_ORDER } from "./brands.js";
import { $, clear, el, placeIndicators } from "./dom.js";
import { icon } from "./icons.js";
import { store, update } from "./store.js";

const WINDOWS = [
  { days: 1, label: "Past 24h" },
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
];

const TICK_COUNT = 4;
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

// One chart at a time is on screen; redraw it when its width changes.
let chartObserver = null;

function buildChart(periods, byPeriod, timeZone, resolution, providerOrder, metric) {
  const order = (providerOrder || PROVIDER_ORDER).filter((provider) => PROVIDER_LABEL[provider]);
  const format = metric === "tokens" ? formatTokens : formatUsd;
  const labelPeriod = (period) => (resolution === "hour" ? formatHourShort(period, timeZone) : formatDayShort(period));
  const wrap = el("div", { class: "usage-chart" });
  const tooltip = el("div", { class: "usage-chart-tip", role: "presentation" });
  const plot = el("div", {
    class: "usage-chart-plot",
    tabIndex: periods.length ? 0 : -1,
    role: "img",
    "aria-label": `${resolution === "hour" ? "Hourly" : "Daily"} ${metric === "tokens" ? "tokens" : "cost"} chart. Use the arrow keys to read each ${resolution === "hour" ? "hour" : "day"}.`,
  });
  const live = el("div", { class: "sr-only", role: "status", "aria-live": "polite" });
  plot.append(tooltip);
  wrap.append(plot, live);

  if (!periods.length) {
    plot.append(el("div", { class: "usage-empty usage-chart-empty", text: "No activity in this window." }));
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
  const active = order.filter((provider, index) => columns.some((column) => column.bands[index].value > 0));
  const peak = columns.reduce((max, column) => column.bands.reduce((inner, band) => Math.max(inner, band.value), max), 0);
  const { max, ticks } = niceScale(peak, TICK_COUNT);
  let hoverIndex = null;
  let geometry = null;

  function draw() {
    const width = plot.clientWidth;
    const height = plot.clientHeight;
    if (!width || !height) return;
    const left = 56;
    const right = 10;
    const top = 12;
    const bottom = 28;
    const innerWidth = Math.max(10, width - left - right);
    const x = (index) => (periods.length === 1 ? left + innerWidth / 2 : left + (index * innerWidth) / (periods.length - 1));
    const y = (value) => (max === 0 ? height - bottom : top + (height - top - bottom) * (1 - value / max));
    const base = height - bottom;
    geometry = { x, y, left, innerWidth, width, top, base };

    plot.querySelector("svg")?.remove();
    const svg = svgEl("svg", { width, height, class: "usage-chart-svg", "aria-hidden": "true" });
    for (const tick of ticks) {
      svg.append(svgEl("line", { x1: left, x2: width - right, y1: y(tick).toFixed(1), y2: y(tick).toFixed(1), class: "usage-grid" }));
      const label = svgEl("text", { x: left - 10, y: (y(tick) + 4).toFixed(1), "text-anchor": "end", class: "usage-axis" });
      label.textContent = tick === 0 ? (metric === "tokens" ? "0" : "$0")
        : metric === "tokens" ? formatTokens(tick) : `$${tick >= 1 ? INTEGER.format(Math.round(tick)) : tick.toFixed(2)}`;
      svg.append(label);
    }
    const labelCount = Math.min(periods.length, width < 480 ? 3 : 5);
    const labelIndexes = new Set(Array.from({ length: labelCount }, (_, step) =>
      labelCount === 1 ? 0 : Math.round((step * (periods.length - 1)) / (labelCount - 1))));
    for (const index of labelIndexes) {
      const text = svgEl("text", {
        x: x(index).toFixed(1),
        y: height - 8,
        "text-anchor": index === 0 && periods.length > 1 ? "start" : index === periods.length - 1 && periods.length > 1 ? "end" : "middle",
        class: "usage-axis",
      });
      text.textContent = labelPeriod(periods[index]);
      svg.append(text);
    }

    const series = active.map((provider) => {
      const bandIndex = order.indexOf(provider);
      const points = columns.map((column, index) => ({ x: x(index), y: y(column.bands[bandIndex].value) }));
      const line = points.length === 1
        ? `M${left},${points[0].y.toFixed(2)} L${width - right},${points[0].y.toFixed(2)}`
        : curvePath(points);
      const total = columns.reduce((sum, column) => sum + column.bands[bandIndex].value, 0);
      return { provider, line, total };
    }).sort((a, b) => b.total - a.total);
    const lastX = periods.length === 1 ? width - right : x(periods.length - 1);
    const firstX = periods.length === 1 ? left : x(0);
    for (const row of series) {
      svg.append(svgEl("path", { d: `${row.line} L${lastX.toFixed(2)},${base} L${firstX.toFixed(2)},${base} Z`, class: `usage-area usage-area-${row.provider}` }));
    }
    for (const row of series) {
      svg.append(svgEl("path", { d: row.line, class: `usage-line usage-line-${row.provider}` }));
    }

    // Label the busiest period so the scale reads at a glance.
    const peakIndex = columns.reduce((best, column, index) => (column.total > columns[best].total ? index : best), 0);
    if (columns[peakIndex].total > 0 && periods.length > 2) {
      const peakY = Math.min(...columns[peakIndex].bands.map((band) => y(band.value)));
      const nearRight = x(peakIndex) > width - 170;
      const note = svgEl("text", {
        x: (x(peakIndex) + (nearRight ? -10 : 10)).toFixed(1),
        y: Math.max(top + 10, peakY + 4).toFixed(1),
        "text-anchor": nearRight ? "end" : "start",
        class: "usage-peak",
      });
      note.textContent = `${labelPeriod(periods[peakIndex])} · ${format(columns[peakIndex].total)}`;
      svg.append(note);
    }

    const hover = svgEl("g", { class: "usage-hover-g" });
    hover.append(svgEl("line", { y1: top, y2: base, class: "usage-hover" }));
    for (const provider of active) hover.append(svgEl("circle", { r: 4.5, class: `usage-dot usage-dot-${provider}`, "data-provider": provider }));
    svg.append(hover);
    plot.prepend(svg);
    showHover(hoverIndex);
  }

  function showHover(index) {
    hoverIndex = index;
    const svg = plot.querySelector("svg");
    const group = svg?.querySelector(".usage-hover-g");
    if (!group || !geometry) return;
    if (index == null) {
      group.classList.remove("on");
      tooltip.classList.remove("on");
      return;
    }
    const { x, y, width } = geometry;
    const column = columns[index];
    const cx = x(index);
    group.classList.add("on");
    const line = group.querySelector("line");
    line.setAttribute("x1", cx.toFixed(1));
    line.setAttribute("x2", cx.toFixed(1));
    for (const dot of group.querySelectorAll("circle")) {
      const band = column.bands.find((item) => item.provider === dot.getAttribute("data-provider"));
      dot.setAttribute("cx", cx.toFixed(1));
      dot.setAttribute("cy", y(band?.value || 0).toFixed(1));
    }
    clear(tooltip);
    tooltip.append(
      el("div", { class: "usage-tip-when", text: labelPeriod(periods[index]) }),
      ...order.map((provider) =>
        el("div", { class: "usage-tip-row" }, [
          el("span", { class: `usage-swatch sw-${provider}`, "aria-hidden": "true" }),
          el("span", { class: "usage-tip-name", text: PROVIDER_LABEL[provider] }),
          el("span", { class: "usage-tip-val", text: format(column.bands.find((band) => band.provider === provider)?.value || 0) }),
        ]),
      ),
      el("div", { class: "usage-tip-row usage-tip-total" }, [
        el("span", { class: "usage-tip-name", text: "Total" }),
        el("span", { class: "usage-tip-val", text: format(column.total) }),
      ]),
    );
    tooltip.classList.add("on");
    const tipWidth = tooltip.offsetWidth || 190;
    const leftPos = cx + 16 + tipWidth > width ? cx - 16 - tipWidth : cx + 16;
    tooltip.style.transform = `translate(${Math.max(0, leftPos).toFixed(0)}px, 0)`;
  }

  const indexAt = (clientX) => {
    if (!geometry) return null;
    const bounds = plot.getBoundingClientRect();
    const fraction = (clientX - bounds.left - geometry.left) / geometry.innerWidth;
    return Math.min(periods.length - 1, Math.max(0, Math.round(fraction * (periods.length - 1))));
  };
  plot.addEventListener("pointermove", (event) => showHover(indexAt(event.clientX)));
  plot.addEventListener("pointerleave", () => showHover(null));
  plot.addEventListener("blur", () => showHover(null));
  plot.addEventListener("keydown", (event) => {
    const step = { ArrowLeft: -1, ArrowRight: 1, Home: -Infinity, End: Infinity }[event.key];
    if (step === undefined) return;
    event.preventDefault();
    const current = hoverIndex ?? (step > 0 ? -1 : periods.length);
    const next = Math.min(periods.length - 1, Math.max(0, current + step));
    showHover(next);
    const column = columns[next];
    live.textContent = `${labelPeriod(periods[next])}: ${order.map((provider) =>
      `${PROVIDER_LABEL[provider]} ${format(column.bands.find((band) => band.provider === provider)?.value || 0)}`).join(", ")}; total ${format(column.total)}`;
  });

  chartObserver?.disconnect();
  if (typeof ResizeObserver === "function") {
    let lastWidth = 0;
    chartObserver = new ResizeObserver(() => {
      if (plot.clientWidth === lastWidth) return;
      lastWidth = plot.clientWidth;
      draw();
    });
    chartObserver.observe(plot);
  } else {
    requestAnimationFrame(draw);
  }
  return wrap;
}

// ------------------------------------------------------------------ render

function segmented(options, current, attr, small = false) {
  const labels = {
    usageDays: "Usage period",
    usageMetric: "Usage metric",
    usageBreakdown: "Usage breakdown",
  };
  return el(
    "div",
    { class: `segmented${small ? " segmented-sm" : ""}`, role: "group", "aria-label": labels[attr] || "Options" },
    [
      el("span", { class: "seg-ind", "aria-hidden": "true" }),
      ...options.map((option) =>
        el("button", {
          class: `seg${option.value === current ? " active" : ""}`,
          type: "button",
          text: option.label,
          "aria-pressed": String(option.value === current),
          dataset: { [attr]: String(option.value) },
        }),
      ),
    ],
  );
}

function metricCard(label, value, detail) {
  return el("div", { class: "card usage-metric" }, [
    el("div", { class: "usage-metric-label", text: label }),
    el("div", { class: "usage-metric-value", text: value }),
    el("div", { class: "usage-metric-detail", text: detail }),
  ]);
}

function windowLabel(summary) {
  if (summary.resolution === "hour" && summary.sinceTime && summary.untilTime) {
    return `${formatDateTimeShort(summary.sinceTime, summary.timeZone)} – ${formatDateTimeShort(summary.untilTime, summary.timeZone)}`;
  }
  return `${formatDayShort(summary.sinceDay)} – ${formatDayShort(summary.untilDay)}`;
}

function bar(share, extra = "") {
  const fill = el("i", { class: `usage-bar-fill${extra ? ` ${extra}` : ""}` });
  fill.style.width = `${Math.max(share * 100, share > 0 ? 1.5 : 0).toFixed(2)}%`;
  return el("span", { class: "usage-bar-track", "aria-hidden": "true" }, [fill]);
}

// "$1,234.56" with quieter currency sign and cents.
function amount(text) {
  const match = /^(\D*)([\d,]+)(\.\d+)?(\D*)$/.exec(text);
  if (!match) return [text];
  return [
    match[1] ? el("span", { class: "q", text: match[1] }) : null,
    match[2],
    match[3] ? el("span", { class: "q", text: match[3] }) : null,
    match[4] ? el("sup", { text: match[4] }) : null,
  ].filter(Boolean);
}

function skeleton() {
  return el("div", { class: "usage-skel", role: "status", "aria-label": "Loading usage data" }, [
    el("div", { class: "usage-top" }, [
      el("div", { class: "card usage-hero" }, [
        el("div", { class: "usage-kicker", text: "Raw token cost" }),
        el("div", { class: "usage-skel-block usage-skel-lg" }),
        el("div", { class: "usage-skel-block usage-skel-sm" }),
        el("div", { class: "usage-skel-block usage-skel-row" }),
      ]),
      el("div", { class: "card usage-tools" }, [
        el("div", { class: "usage-skel-block usage-skel-row" }),
        el("div", { class: "usage-skel-block usage-skel-row" }),
        el("div", { class: "usage-skel-block usage-skel-row" }),
      ]),
    ]),
    el("p", { class: "usage-scan", text: "Reading usage from configured machines…" }),
  ]);
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
    requestAnimationFrame(() => placeIndicators(root));
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

  root.append(el("div", { class: "usage-head" }, [
    el("div", { class: "usage-head-copy" }, [
      el("h1", { class: "title", text: "Usage" }),
      el("p", {
        class: "usage-range",
        text: summary ? `${windowLabel(summary)} · all configured machines` : "Usage across configured machines",
      }),
    ]),
    el("div", { class: "usage-head-actions" }, [
      segmented(WINDOWS.map((item) => ({ value: item.days, label: item.label })), days, "usageDays"),
    ]),
  ]));

  if (error) {
    root.append(el("div", { class: "usage-error", role: "alert", text: error }));
    return;
  }
  if (loading || !summary) {
    root.append(skeleton());
    return;
  }

  for (const machine of summary.machines || []) {
    if (machine.status !== "ok") {
      root.append(el("div", { class: "usage-error", role: "status", text: `${machine.id} is not included. ${machine.message || "Could not read usage."} The totals below are partial.` }));
    }
    if (machine.status === "ok" && machine.pricing?.status === "unavailable") {
      root.append(el("div", { class: "usage-error", role: "status", text: `${machine.id}: model prices are unavailable. Its token counts are included, but its estimated cost is incomplete.` }));
    }
  }

  const report = summary.rollups;
  const merged = report.total;
  const sources = report.bySource;
  const models = report.byModel;
  const machineRows = report.byMachine || [];
  const periods = new Map(report.periods.map((period) => [period.key, period]));
  const sourceOrder = sources.map((row) => row.source);
  const cost = metric === "cost";
  const orderedSources = [...sources].sort((a, b) => (cost ? b.costUsd - a.costUsd : b.totalTokens - a.totalTokens));
  const periodKeys =
    hourly && summary.sinceTime && summary.untilTime
      ? enumerateHourStarts(summary.sinceTime, summary.untilTime)
      : enumerateDays(summary.sinceDay, summary.untilDay);
  const activePeriods = periodKeys.filter((keyName) => (periods.get(keyName)?.totalTokens || 0) > 0).length;
  const periodAverage = activePeriods === 0 ? 0 : merged.totalTokens / activePeriods;
  const observedInput = merged.uncachedInputTokens + merged.cachedInputTokens;
  const cachedShare = observedInput === 0 ? 0 : merged.cachedInputTokens / observedInput;
  const recent = [...periodKeys].reverse().slice(0, 8);
  const sourceProblems = (provider) => (summary.sources || []).filter(
    (source) => source.provider === provider && source.status !== "ok");

  // ---- hero: total and machines | tools
  const machineTotal = machineRows.reduce((sum, row) => sum + (cost ? row.costUsd : row.totalTokens), 0);
  const hero = el("div", { class: "card usage-hero" }, [
    el("div", {}, [
      el("div", { class: "usage-kicker", text: cost ? "Raw token cost" : "Processed tokens" }),
      el("div", { class: "usage-hero-value" }, cost ? amount(`${formatUsd(merged.costUsd)}*`) : [formatTokens(merged.totalTokens)]),
      el("div", {
        class: "usage-hero-note",
        text: cost
          ? "* API cost estimate. This is not your subscription bill."
          : `Input, cache reads and output across ${formatCount(merged.sessions)} sessions.`,
      }),
    ]),
    machineRows.length ? el("div", { class: "usage-machines" }, [
      el("div", { class: "usage-kicker", text: "By machine" }),
      ...machineRows.map((row) => {
        const value = cost ? row.costUsd : row.totalTokens;
        return el("div", { class: "usage-machine" }, [
          el("span", { class: "usage-machine-name", text: row.machine }),
          bar(machineTotal ? value / machineTotal : 0, "is-neutral"),
          el("span", { class: "usage-machine-val" }, [
            el("b", { text: cost ? formatUsd(row.costUsd) : formatTokens(row.totalTokens) }),
            el("small", { text: `${cost ? `${formatTokens(row.totalTokens)} tokens` : formatUsd(row.costUsd)} · ${formatCount(row.sessions)} sessions` }),
          ]),
        ]);
      }),
    ]) : null,
  ]);

  const tools = el("div", { class: "card usage-tools" }, orderedSources.length
    ? orderedSources.map((row) => {
      const share = cost ? row.costShare : row.tokenShare;
      const problems = sourceProblems(row.source);
      const used = row.totalTokens > 0 || row.costUsd > 0;
      return el("div", { class: `usage-tool${used ? "" : " is-idle"}` }, [
        el("span", { class: `usage-tool-ico usage-mark usage-mark-${row.source}`, html: MARK[row.source] || "" }),
        el("div", { class: "usage-tool-copy" }, [
          el("div", { class: "usage-tool-name" }, [
            PROVIDER_LABEL[row.source] || row.source,
            problems.length ? el("span", { class: "tag tag-warn", text: "Partial" }) : null,
          ]),
          used
            ? el("div", {
              class: "usage-tool-sub",
              text: cost
                ? `${formatPercent(share)} of cost · ${formatTokens(row.totalTokens)} tokens`
                : `${formatPercent(share)} of tokens · ${formatUsd(row.costUsd)}`,
            })
            : el("div", { class: "usage-tool-sub", text: "No usage in this period" }),
          used ? bar(share, `sw-${row.source}`) : null,
          ...problems.map((source) => el("div", { class: "usage-tool-warn" }, [
            el("span", { "aria-hidden": "true", html: icon("warn") }),
            `${source.message || "Could not report usage"} on ${source.machine}`,
          ])),
        ]),
        el("span", { class: "usage-tool-val", text: cost ? formatUsd(row.costUsd) : formatTokens(row.totalTokens) }),
      ]);
    })
    : [el("div", { class: "usage-empty", text: "No activity in this window." })]);
  root.append(el("div", { class: "usage-top" }, [hero, tools]));

  // ---- KPIs
  const savingsDetail =
    merged.costUsd > 0 && merged.cacheSavingsUsd > merged.costUsd
      ? `${(merged.cacheSavingsUsd / merged.costUsd).toFixed(1)}× the raw token cost`
      : "vs full input rates";
  root.append(
    el("div", { class: "usage-metrics" }, [
      metricCard(
        "Processed tokens",
        formatTokens(merged.totalTokens),
        `${formatCount(merged.sessions)} sessions · ${formatTokens(periodAverage)}/${hourly ? "hour" : "day"}`,
      ),
      metricCard("Cached input", formatTokens(merged.cachedInputTokens), `${formatPercent(cachedShare)} of observed input`),
      metricCard("Uncached input", formatTokens(merged.uncachedInputTokens), `${formatTokens(merged.cacheCreationTokens)} cache writes`),
      metricCard("Output", formatTokens(merged.outputTokens), `${formatTokens(merged.reasoningTokens)} reasoning`),
      metricCard("Cache savings", formatUsd(merged.cacheSavingsUsd), savingsDetail),
    ]),
  );

  // ---- chart
  root.append(
    el("section", { class: "card usage-chart-card" }, [
      el("div", { class: "usage-chart-head" }, [
        el("h2", { class: "usage-card-title", text: `${hourly ? "Hourly" : "Daily"} ${metric === "tokens" ? "tokens" : "cost"}` }),
        el("div", { class: "usage-legend" }, sourceOrder.filter((source) => PROVIDER_LABEL[source]).map((source) =>
          el("span", { class: "usage-legend-item" }, [
            el("span", { class: `usage-swatch sw-${source}`, "aria-hidden": "true" }),
            el("span", { text: PROVIDER_LABEL[source] }),
          ]))),
        segmented([{ value: "cost", label: "Cost" }, { value: "tokens", label: "Tokens" }], metric, "usageMetric", true),
      ]),
      buildChart(periodKeys, periods, summary.timeZone, summary.resolution, sourceOrder, metric),
    ]),
  );

  // ---- breakdown
  const tableHead = el("div", { class: "sec-head" }, [
    el("h2", { class: "sec-title", text: "Breakdown" }),
    el("span", { class: "sec-sub", text: breakdown === "model" ? `${models.length} model${models.length === 1 ? "" : "s"}` : `last ${recent.length} ${hourly ? "hours" : "days"}` }),
    el("span", { class: "spacer" }),
    segmented([{ value: "model", label: "By model" }, { value: "time", label: hourly ? "By hour" : "By day" }], breakdown, "usageBreakdown", true),
  ]);

  let table;
  if (breakdown === "model") {
    const topShare = models.reduce((best, row) => Math.max(best, row.costShare || 0), 0) || 1;
    table = el("table", { class: "usage-table" }, [
      el("thead", {}, [
        el("tr", {}, [
          el("th", { text: "Model" }),
          el("th", { class: "hide-sm", text: "Tool" }),
          el("th", { class: "num", text: "Cost" }),
          el("th", { text: "Share" }),
          el("th", { class: "num", text: "Tokens" }),
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
                  el("span", { class: `usage-swatch sw-${row.source}`, "aria-hidden": "true" }),
                  el("span", { class: "mono", text: row.model }),
                ]),
              ]),
              el("td", { class: "hide-sm usage-dim", text: PROVIDER_LABEL[row.source] || row.source }),
              el("td", { class: "num", text: formatUsd(row.costUsd) }),
              el("td", {}, [el("div", { class: "usage-share" }, [bar((row.costShare || 0) / topShare, `sw-${row.source}`), el("span", { class: "num", text: formatPercent(row.costShare) })])]),
              el("td", { class: "num usage-dim", text: formatTokens(row.totalTokens) }),
            ]),
          )
          : [el("tr", {}, [el("td", { colSpan: 5, class: "usage-empty", text: "No activity in this window." })])],
      ),
    ]);
  } else {
    table = el("table", { class: "usage-table" }, [
      el("thead", {}, [
        el("tr", {}, [
          el("th", { text: hourly ? "Hour" : "Day" }),
          ...sourceOrder.map((source) => el("th", { class: "num", text: PROVIDER_LABEL[source] || source })),
          el("th", { class: "num", text: "Total" }),
          el("th", { class: "num", text: "Tokens" }),
        ]),
      ]),
      el(
        "tbody",
        {},
        recent.length
          ? recent.map((keyName) => {
            const period = periods.get(keyName);
            return el("tr", {}, [
              el("td", { text: hourly ? formatHourShort(keyName, summary.timeZone) : formatDayShort(keyName) }),
              ...sourceOrder.map((source) =>
                el("td", { class: "num usage-dim", text: formatUsd(period?.bySource?.[source]?.costUsd || 0) }),
              ),
              el("td", { class: "num", text: formatUsd(period?.costUsd || 0) }),
              el("td", { class: "num usage-dim", text: formatTokens(period?.totalTokens || 0) }),
            ]);
          })
          : [el("tr", {}, [el("td", { colSpan: 3 + sourceOrder.length, class: "usage-empty", text: "No activity in this window." })])],
      ),
    ]);
  }
  root.append(el("section", { class: "sec" }, [tableHead, el("div", { class: "card usage-table-card" }, [table])]));

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
    const label = PROVIDER_LABEL[source.provider] || source.provider;
    const where = source.machine ? `${source.machine} · ${label}` : label;
    if (source.status === "failed") notes.push(`${where}: ${source.message || "could not report usage."}`);
    else if (source.status === "missing") notes.push(`${where}: ${source.message || "no transcript directory."}`);
    else if (source.provider !== "hub") notes.push(`${where}: ${formatCount(source.scannedFiles)} ${source.provider === "cursor" ? "account events" : "files"}, ${formatCount(source.sessions)} sessions.`);
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
