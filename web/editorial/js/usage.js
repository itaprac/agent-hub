// Usage tab: Claude Code + Codex transcript spend, laid out like T3 Code's
// Usage page and painted with this console's tokens.

import { api } from "./api.js";
import { MARK, PROVIDER_LABEL } from "./brands.js";
import { $, clear, el } from "./dom.js";
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

function formatDayLong(day) {
  const date = new Date(`${day}T12:00:00Z`);
  if (Number.isNaN(date.getTime())) return day || "";
  return new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "short", month: "short", day: "numeric" }).format(date);
}

function swatch(provider) {
  return el("i", { class: `sw s-${provider}`, "aria-hidden": "true" });
}

// Line chart drawn at the real pixel width, so text and markers keep their shape.
// Hover or arrow keys move a crosshair with one dot per series and a tooltip.
let chartObserver = null;

function drawChart(host, model) {
  const width = Math.max(280, Math.round(host.clientWidth || 900));
  const narrow = width < 560;
  const height = narrow ? 220 : 280;
  const left = 56;
  const right = 10;
  const top = 18;
  const bottom = 30;
  const { periods, series, format, label, longLabel } = model;
  const count = periods.length;
  clear(host);
  const svg = svgEl("svg", {
    class: "chart-svg", width, height, viewBox: `0 0 ${width} ${height}`, role: "img",
    "aria-label": model.summary,
  });
  const tip = el("div", { class: "tip", "aria-hidden": "true" });
  host.append(svg, tip);
  if (!count) return;

  const peak = series.reduce((max, row) => row.values.reduce((inner, value) => Math.max(inner, value), max), 0);
  const { max, ticks } = niceScale(peak, TICK_COUNT);
  const plotW = width - left - right;
  const plotH = height - top - bottom;
  const x = (index) => left + (count === 1 ? plotW / 2 : (index * plotW) / (count - 1));
  const y = (value) => top + (max === 0 ? plotH : plotH * (1 - value / max));

  const grid = svgEl("g", { class: "grid" });
  const axis = svgEl("g", { class: "axis" });
  for (const tick of ticks) {
    grid.append(svgEl("line", { x1: left, x2: width - right, y1: y(tick).toFixed(1), y2: y(tick).toFixed(1), class: tick === 0 ? "base" : null }));
    const text = svgEl("text", { x: left - 10, y: (y(tick) + 4).toFixed(1), "text-anchor": "end" });
    text.textContent = tick === 0 ? "0" : (model.axisFormat || format)(tick);
    axis.append(text);
  }
  const labelAt = narrow ? [0, Math.floor((count - 1) / 2), count - 1] : [0, 1, 2, 3, 4].map((part) => Math.round((part * (count - 1)) / 4));
  for (const index of [...new Set(labelAt)]) {
    const anchor = index === 0 ? "start" : index === count - 1 ? "end" : "middle";
    const text = svgEl("text", { x: x(index).toFixed(1), y: height - 8, "text-anchor": count === 1 ? "middle" : anchor });
    text.textContent = label(periods[index]);
    axis.append(text);
  }
  svg.append(grid, axis);

  const drawn = [...series].sort((a, b) => b.total - a.total);
  for (const row of drawn) {
    const points = row.values.map((value, index) => ({ x: x(index), y: y(value) }));
    const line = count === 1 ? `M${left},${points[0].y} L${width - right},${points[0].y}` : curvePath(points);
    svg.append(svgEl("path", { d: `${line} L${x(count - 1)},${y(0)} L${x(0)},${y(0)} Z`, class: `area s-${row.provider}` }));
  }
  for (const row of drawn) {
    const points = row.values.map((value, index) => ({ x: x(index), y: y(value) }));
    const line = count === 1 ? `M${left},${points[0].y} L${width - right},${points[0].y}` : curvePath(points);
    svg.append(svgEl("path", { d: line, class: `line s-${row.provider}` }));
  }

  // Direct label on the busiest period.
  const totals = periods.map((_, index) => series.reduce((sum, row) => sum + row.values[index], 0));
  const peakIndex = totals.reduce((best, value, index) => (value > totals[best] ? index : best), 0);
  if (totals[peakIndex] > 0 && series.length) {
    const high = Math.max(...series.map((row) => row.values[peakIndex]));
    const px = x(peakIndex);
    const endSide = px > width - 200;
    const text = svgEl("text", {
      x: (px + (endSide ? -10 : 10)).toFixed(1), y: Math.max(12, y(high) - 2).toFixed(1),
      "text-anchor": endSide ? "end" : "start", class: "peak",
    });
    text.textContent = `${format(totals[peakIndex])} · ${label(periods[peakIndex])}`;
    svg.append(text);
  }

  const hover = svgEl("g", { class: "hover", visibility: "hidden" });
  const cross = svgEl("line", { y1: top, y2: height - bottom, class: "cross" });
  hover.append(cross);
  const dots = drawn.map((row) => {
    const dot = svgEl("circle", { r: 4.5, class: `pt s-${row.provider}` });
    hover.append(dot);
    return { row, dot };
  });
  svg.append(hover);
  const hit = svgEl("rect", { x: left, y: 0, width: plotW, height, class: "hit" });
  svg.append(hit);

  let current = null;
  const show = (index) => {
    host.classList.toggle("hovering", index !== null);
    if (index === null) {
      current = null;
      hover.setAttribute("visibility", "hidden");
      tip.classList.remove("on");
      return;
    }
    current = index;
    const px = x(index);
    hover.setAttribute("visibility", "visible");
    cross.setAttribute("x1", px.toFixed(1));
    cross.setAttribute("x2", px.toFixed(1));
    for (const { row, dot } of dots) {
      dot.setAttribute("cx", px.toFixed(1));
      dot.setAttribute("cy", y(row.values[index]).toFixed(1));
    }
    clear(tip);
    tip.append(
      el("div", { class: "d", text: longLabel(periods[index]) }),
      ...series.map((row) => el("div", { class: "r" }, [swatch(row.provider), el("span", { text: row.label }), el("b", { text: format(row.values[index]) })])),
      el("div", { class: "r t" }, [el("span", { text: "Total" }), el("b", { text: format(totals[index]) })]),
    );
    tip.classList.add("on");
    const tw = tip.offsetWidth;
    const th = tip.offsetHeight;
    const leftPos = px + 16 + tw > width ? px - tw - 16 : px + 16;
    const high = Math.max(0, ...series.map((row) => row.values[index]));
    tip.style.left = `${Math.max(0, leftPos)}px`;
    tip.style.top = `${Math.max(0, Math.min(y(high) - 24, height - bottom - th))}px`;
  };
  const indexAt = (clientX) => {
    const box = svg.getBoundingClientRect();
    const scale = box.width ? width / box.width : 1;
    const px = (clientX - box.left) * scale;
    return count === 1 ? 0 : Math.max(0, Math.min(count - 1, Math.round(((px - left) / plotW) * (count - 1))));
  };
  hit.addEventListener("pointermove", (event) => show(indexAt(event.clientX)));
  hit.addEventListener("pointerdown", (event) => show(indexAt(event.clientX)));
  hit.addEventListener("pointerleave", (event) => {
    if (event.pointerType === "mouse") show(null);
  });
  host.onkeydown = (event) => {
    const step = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      show(event.key === "Home" ? 0 : count - 1);
    } else if (step) {
      event.preventDefault();
      show(Math.max(0, Math.min(count - 1, (current ?? (step > 0 ? -1 : count)) + step)));
    } else if (event.key === "Escape" && current !== null) {
      event.stopPropagation();
      show(null);
    }
  };
  host.onblur = () => show(null);
}

function mountChart(host, model) {
  drawChart(host, model);
  if (chartObserver) chartObserver.disconnect();
  if (typeof ResizeObserver !== "function") return;
  let lastWidth = host.clientWidth;
  let frame = 0;
  chartObserver = new ResizeObserver(() => {
    if (!host.isConnected || host.clientWidth === lastWidth || !host.clientWidth) return;
    lastWidth = host.clientWidth;
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => drawChart(host, model));
  });
  chartObserver.observe(host);
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
    { class: "seg", role: "group", "aria-label": labels[attr] || "Options" },
    options.map((option) =>
      el("button", {
        type: "button",
        text: option.label,
        "aria-pressed": String(option.value === current),
        dataset: { [attr]: String(option.value) },
      }),
    ),
  );
}

function kpi(label, value, detail) {
  return el("div", {}, [
    el("small", { text: label }),
    el("b", { text: value }),
    el("span", { text: detail }),
  ]);
}

function windowLabel(summary) {
  if (summary.resolution === "hour" && summary.sinceTime && summary.untilTime) {
    return `${formatDateTimeShort(summary.sinceTime, summary.timeZone)} – ${formatDateTimeShort(summary.untilTime, summary.timeZone)}`;
  }
  return `${formatDayShort(summary.sinceDay)} – ${formatDayShort(summary.untilDay)}`;
}

function skeleton() {
  return el("div", { class: "usage-skel", role: "status", "aria-label": "Loading usage data" }, [
    el("div", { class: "skel skel-big" }),
    el("div", { class: "skel skel-line" }),
    el("div", { class: "skel skel-chart" }),
    el("p", { class: "foot-note", text: "Reading usage from configured machines…" }),
  ]);
}

function sourcesSection(summary) {
  const sources = (summary.sources || []).filter((source) => source.provider !== "hub");
  if (!sources.length) return null;
  return el("section", { class: "sec" }, [
    el("div", { class: "sec-head" }, [el("h2", { text: "Sources" }), el("p", { text: `Scanned in ${formatCount(summary.scanDurationMs)} ms` })]),
    el("div", { class: "group" }, sources.map((source) => {
      const ok = source.status === "ok";
      return el("div", { class: `item${ok ? "" : " t-warn"}` }, [
        el("span", { class: `ic ${ok ? "neutral" : "t-warn"}`, html: `<svg class="i" aria-hidden="true"><use href="#i-${ok ? "check" : "alert"}"/></svg>` }),
        el("span", { class: "item-main" }, [
          el("span", { class: "item-name", text: `${PROVIDER_LABEL[source.provider] || source.provider}` }),
          el("span", { class: "tag", text: source.machine || "" }),
          el("span", { class: "path mono", text: source.path || "" }),
        ]),
        el("span", { class: `right${ok ? "" : " t-warn"}`, text: ok
          ? `${formatCount(source.scannedFiles)} ${source.provider === "cursor" ? "account events" : "files"} · ${formatCount(source.sessions)} sessions`
          : source.message || "Could not report usage" }),
      ]);
    })),
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
  const costMetric = metric === "cost";

  root.append(el("div", { class: "hero hero-top" }, [
    el("div", { class: "hero-copy" }, [
      el("p", { class: "eyebrow", text: "Usage" }),
      el("h1", { text: costMetric ? "Raw token cost" : "Processed tokens" }),
      el("p", { class: "lede", text: summary ? `${windowLabel(summary)} · Configured machines` : "Usage across configured machines" }),
    ]),
    segmented(WINDOWS.map((item) => ({ value: item.days, label: item.label })), days, "usageDays"),
  ]));

  if (error) {
    root.append(el("div", { class: "usage-error", role: "alert", text: error }));
    return;
  }
  if (loading || !summary) {
    root.append(skeleton());
    return;
  }

  const notices = [];
  for (const machine of summary.machines || []) {
    if (machine.status !== "ok") notices.push(`${machine.id} is not included. ${machine.message || "Could not read usage."} The totals below are partial.`);
    if (machine.status === "ok" && machine.pricing?.status === "unavailable") notices.push(`${machine.id}: model prices are unavailable. Its token counts are included, but its estimated cost is incomplete.`);
  }
  for (const text of notices) root.append(el("div", { class: "usage-error", role: "status", text }));

  const report = summary.rollups;
  const merged = report.total;
  const sources = report.bySource;
  const models = report.byModel;
  const periods = new Map(report.periods.map((period) => [period.key, period]));
  const sourceOrder = sources.map((row) => row.source);
  const periodKeys =
    hourly && summary.sinceTime && summary.untilTime
      ? enumerateHourStarts(summary.sinceTime, summary.untilTime)
      : enumerateDays(summary.sinceDay, summary.untilDay);
  const activePeriods = periodKeys.filter((keyName) => (periods.get(keyName)?.totalTokens || 0) > 0).length;
  const periodAverage = activePeriods === 0 ? 0 : merged.totalTokens / activePeriods;
  const observedInput = merged.uncachedInputTokens + merged.cachedInputTokens;
  const cachedShare = observedInput === 0 ? 0 : merged.cachedInputTokens / observedInput;
  const recent = [...periodKeys].reverse().slice(0, hourly ? 24 : 14);
  const format = costMetric ? formatUsd : formatTokens;
  const valueOf = (row) => (costMetric ? row?.costUsd || 0 : row?.totalTokens || 0);

  // Hero figure.
  root.append(
    el("div", { class: "bignum", text: costMetric ? formatUsd(merged.costUsd) : formatTokens(merged.totalTokens) }, [
      costMetric ? el("sup", { text: "*", "aria-hidden": "true" }) : null,
    ]),
    el("p", {
      class: "foot-note",
      text: costMetric
        ? "* API cost estimate. This is not your subscription bill."
        : `Input, cache reads and output across ${formatCount(merged.sessions)} sessions.`,
    }),
  );
  const machineRows = report.byMachine || [];
  if (machineRows.length) {
    root.append(el("div", { class: "bymachine" }, machineRows.map((row) => el("div", {}, [
      el("b", { text: format(valueOf(row)) }),
      `${row.machine} · ${costMetric ? `${formatTokens(row.totalTokens)} tokens` : formatUsd(row.costUsd)} · ${formatCount(row.sessions)} sessions`,
    ]))));
  }

  // Chart.
  const series = sourceOrder
    .map((provider) => ({
      provider,
      label: PROVIDER_LABEL[provider] || provider,
      values: periodKeys.map((keyName) => valueOf(periods.get(keyName)?.bySource?.[provider])),
    }))
    .map((row) => ({ ...row, total: row.values.reduce((sum, value) => sum + value, 0) }))
    .filter((row) => row.total > 0);
  const labelPeriod = (period) => (hourly ? formatHourShort(period, summary.timeZone) : formatDayShort(period));
  const longPeriod = (period) => (hourly ? formatDateTimeShort(period, summary.timeZone) : formatDayLong(period));
  const chartHost = el("div", {
    class: "chart", tabIndex: 0,
    "aria-label": `${hourly ? "Hourly" : "Daily"} ${costMetric ? "cost" : "tokens"} chart. Use the arrow keys to read each ${hourly ? "hour" : "day"}.`,
  });
  root.append(el("section", { class: "sec chart-sec" }, [
    el("div", { class: "sec-head" }, [
      el("h2", { text: `${hourly ? "Hourly" : "Daily"} ${costMetric ? "cost" : "tokens"}` }),
      el("div", { class: "legend" }, series.map((row) => el("span", {}, [swatch(row.provider), row.label]))),
      segmented([{ value: "cost", label: "Cost" }, { value: "tokens", label: "Tokens" }], metric, "usageMetric"),
    ]),
    chartHost,
  ]));
  const model = {
    periods: periodKeys, series, format, label: labelPeriod, longLabel: longPeriod,
    axisFormat: costMetric ? (value) => (value >= 1000 ? `$${trim(value / 1000)}k` : `$${trim(value)}`) : formatTokens,
    summary: `${hourly ? "Hourly" : "Daily"} ${costMetric ? "cost" : "tokens"} for ${series.map((row) => row.label).join(" and ") || "no tools"}, ${windowLabel(summary)}.`,
  };
  requestAnimationFrame(() => {
    if (chartHost.isConnected) mountChart(chartHost, model);
  });

  // Tool shares.
  const shareOf = (row) => (costMetric ? row.costShare : row.tokenShare) || 0;
  const ordered = [...sources].sort((a, b) => valueOf(b) - valueOf(a));
  const failures = new Map();
  for (const source of summary.sources || []) {
    if (source.status === "failed") failures.set(source.provider, source.message || "Could not report usage");
  }
  root.append(el("section", { class: "sec" }, [
    el("div", { class: "sec-head" }, [el("h2", { text: "By tool" }), el("p", { text: `Share of ${costMetric ? "cost" : "tokens"}` })]),
    el("div", { class: "stack", role: "img", "aria-label": ordered.map((row) => `${PROVIDER_LABEL[row.source] || row.source} ${formatPercent(shareOf(row))}`).join(", ") },
      ordered.filter((row) => shareOf(row) > 0).map((row) => el("i", { class: `s-${row.source}`, style: { width: `${shareOf(row) * 100}%` } }))),
    ordered.length
      ? el("div", { class: "tools" }, ordered.map((row) => el("div", {}, [
        el("b", {}, [swatch(row.source), PROVIDER_LABEL[row.source] || row.source]),
        el("div", { class: "v", text: format(valueOf(row)) }),
        el("span", { text: `${formatPercent(shareOf(row))} · ${costMetric ? `${formatTokens(row.totalTokens)} tokens` : formatUsd(row.costUsd)}` }),
        failures.has(row.source) ? el("div", { class: "warnline" }, [
          el("span", { html: '<svg class="i" aria-hidden="true"><use href="#i-alert"/></svg>' }),
          el("span", { text: failures.get(row.source) }),
        ]) : null,
      ])))
      : el("p", { class: "empty-line", text: "No activity in this window." }),
  ]));

  // KPIs.
  const savingsDetail =
    merged.costUsd > 0 && merged.cacheSavingsUsd > merged.costUsd
      ? `${(merged.cacheSavingsUsd / merged.costUsd).toFixed(1)}× the raw token cost`
      : "vs full input rates";
  root.append(el("section", { class: "sec" }, [el("div", { class: "kpis" }, [
    kpi("Processed tokens", formatTokens(merged.totalTokens), `${formatCount(merged.sessions)} sessions · ${formatTokens(periodAverage)}/${hourly ? "hour" : "day"}`),
    kpi("Cached input", formatTokens(merged.cachedInputTokens), `${formatPercent(cachedShare)} of observed input`),
    kpi("Uncached input", formatTokens(merged.uncachedInputTokens), `${formatTokens(merged.cacheCreationTokens)} cache writes`),
    kpi("Output", formatTokens(merged.outputTokens), `${formatTokens(merged.reasoningTokens)} reasoning`),
    kpi("Cache savings", formatUsd(merged.cacheSavingsUsd), savingsDetail),
  ])]));

  // Breakdown.
  let table;
  if (breakdown === "model") {
    const top = Math.max(0, ...models.map((row) => row.costShare || 0));
    table = el("table", { class: "usage-table" }, [
      el("thead", {}, [el("tr", {}, [
        el("th", { scope: "col", text: "Model" }),
        el("th", { scope: "col", class: "n", text: "Cost" }),
        el("th", { scope: "col", class: "n", text: "Share" }),
        el("th", { scope: "col", class: "n col-tok", text: "Tokens" }),
      ])]),
      el("tbody", {}, models.length
        ? models.map((row) => el("tr", {}, [
          el("td", {}, [el("span", { class: "usage-model" }, [
            el("span", { class: `usage-mark usage-mark-${row.source}`, html: MARK[row.source] || "" }),
            el("span", { class: "mono", text: row.model }),
          ])]),
          el("td", { class: "n", text: formatUsd(row.costUsd) }),
          el("td", { class: "n" }, [
            el("span", { class: "sharebar", "aria-hidden": "true" }, [el("i", { style: { width: `${top ? ((row.costShare || 0) / top) * 100 : 0}%` } })]),
            formatPercent(row.costShare),
          ]),
          el("td", { class: "n col-tok", text: formatTokens(row.totalTokens) }),
        ]))
        : [el("tr", {}, [el("td", { colSpan: 4, class: "usage-empty", text: "No activity in this window." })])]),
    ]);
  } else {
    table = el("table", { class: "usage-table" }, [
      el("thead", {}, [el("tr", {}, [
        el("th", { scope: "col", text: hourly ? "Hour" : "Day" }),
        ...sourceOrder.map((source) => el("th", { scope: "col", class: "n col-src", text: PROVIDER_LABEL[source] || source })),
        el("th", { scope: "col", class: "n", text: "Total" }),
        el("th", { scope: "col", class: "n col-tok", text: "Tokens" }),
      ])]),
      el("tbody", {}, recent.length
        ? recent.map((keyName) => {
          const period = periods.get(keyName);
          return el("tr", {}, [
            el("td", { text: hourly ? formatHourShort(keyName, summary.timeZone) : formatDayLong(keyName) }),
            ...sourceOrder.map((source) => el("td", { class: "n col-src", text: formatUsd(period?.bySource?.[source]?.costUsd || 0) })),
            el("td", { class: "n", text: formatUsd(period?.costUsd || 0) }),
            el("td", { class: "n col-tok", text: formatTokens(period?.totalTokens || 0) }),
          ]);
        })
        : [el("tr", {}, [el("td", { colSpan: 3 + sourceOrder.length, class: "usage-empty", text: "No activity in this window." })])]),
    ]);
  }
  root.append(el("section", { class: "sec" }, [
    el("div", { class: "sec-head" }, [
      el("h2", { text: "Breakdown" }),
      el("p", { text: breakdown === "model" ? `${models.length} model${models.length === 1 ? "" : "s"}` : `Last ${recent.length} ${hourly ? "hours" : "days"}` }),
      segmented([{ value: "model", label: "By model" }, { value: "time", label: hourly ? "By hour" : "By day" }], breakdown, "usageBreakdown"),
    ]),
    el("div", { class: "table-wrap" }, [table]),
  ]));

  const sourcesBlock = sourcesSection(summary);
  if (sourcesBlock) root.append(sourcesBlock);

  const notes = [];
  const unpricedTokens = models.reduce((sum, row) => (row.unpricedRecords ? sum + row.totalTokens : sum), 0);
  if (unpricedTokens > 0) notes.push(`${formatTokens(unpricedTokens)} tokens had no published API rate and were left unpriced.`);
  if (summary.pricing?.status && !["ok", "fresh", "cached"].includes(summary.pricing.status)) notes.push(`Model rates: ${summary.pricing.status}.`);
  for (const source of summary.sources || []) {
    const label = PROVIDER_LABEL[source.provider] || source.provider;
    if (source.status === "missing") notes.push(`${label}: ${source.message || "no transcript directory."}`);
  }
  if (notes.length) root.append(el("p", { class: "usage-foot", text: notes.join(" · ") }));
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
