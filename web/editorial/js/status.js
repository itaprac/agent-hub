// Render structured status checks and command output.

import { $, $$, clear, el, formatTime } from "./dom.js";
import { update } from "./store.js";

const TONES = {
  ok: "ok",
  link: "info",
  copy: "info",
  prune: "info",
  render: "info",
  commit: "info",
  pull: "info",
  push: "info",
  skip: "skip",
  MISSING: "warn",
  DRIFT: "bad",
  STALE: "bad",
  ERROR: "bad",
  CONFLICT: "bad",
  warn: "warn",
};

const PROBLEMS = new Set(["MISSING", "DRIFT", "STALE", "ERROR", "CONFLICT"]);
const ACTIONS = new Set(["link", "copy", "prune", "render", "commit", "pull", "push"]);

export const tone = (level) => TONES[level] || "plain";
export const isProblem = (level) => PROBLEMS.has(level);

const homePath = (path) => String(path || "").replace(/^\/(?:Users|home)\/[^/]+(?=\/|$)/, "~");

function statusRow(check) {
  const group = check.agent || (check.project || check.kind === "project" ? "projects" : check.kind);
  const project = check.project ? String(check.project).replace(/--/g, "/") : "";
  const scope = project ? `project ${project}` : check.agent ? "global" : check.kind;
  return {
    ...check,
    group,
    label: check.name ? `${scope}/${check.name}` : scope,
    detail: check.text || check.target || "",
    where: homePath(check.target),
  };
}

export function summarize(result) {
  const counts = {};
  let checks = 0;
  let problems = 0;
  let actions = 0;
  for (const line of result?.checks || []) {
    if (!line.level) continue;
    counts[line.level] = (counts[line.level] || 0) + 1;
    checks += 1;
    if (PROBLEMS.has(line.level)) problems += 1;
    if (ACTIONS.has(line.level)) actions += 1;
  }
  return { counts, checks, problems, actions };
}

function groupOrder(state) {
  const agents = (state?.agents || []).map((agent) => agent.name);
  return (name) => {
    if (name === "git") return [3, name];
    if (name === "projects") return [2, name];
    const index = agents.indexOf(name);
    return index >= 0 ? [0, String(index).padStart(3, "0")] : [1, name];
  };
}

function groupKind(name, state) {
  if (name === "git") return "vcs";
  if (name === "projects") return "config";
  const agent = (state?.agents || []).find((item) => item.name === name);
  return agent ? agent.mode : "";
}

// One compact line: verdict, check count, and non-zero problem counters.
// Quiet levels (ok, skip, actions) only show up in the meta text and the tooltip.
const PROBLEM_COUNTERS = [
  ["MISSING", "missing", "warn"],
  ["DRIFT", "drift", "bad"],
  ["STALE", "stale", "bad"],
  ["ERROR", "error", "bad"],
];

const ICON = (name) => `<svg class="i" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const LEVEL_WORD = {
  ok: "OK", skip: "Skipped", MISSING: "Missing", DRIFT: "Drift", STALE: "Stale", ERROR: "Error", CONFLICT: "Conflict",
  link: "Linked", copy: "Copied", prune: "Pruned", render: "Rendered", commit: "Committed", pull: "Pulled", push: "Pushed", warn: "Warning",
};

function summaryMeta(counts, result) {
  const parts = [];
  if (counts.ok) parts.push(`${counts.ok} ok`);
  const actions = [...ACTIONS].reduce((sum, level) => sum + (counts[level] || 0), 0);
  if (actions) parts.push(`${actions} actions`);
  if (counts.skip) parts.push(`${counts.skip} skipped`);
  parts.push(`exit ${result.exit_code}`);
  if (result.at) parts.push(result.at);
  return parts.join(" · ");
}

function renderSummary(host, result, state) {
  clear(host);
  if (!result) {
    host.className = "statusbar is-idle";
    host.title = "";
    host.append(
      el("span", { class: "statusbar-verdict", text: "No status yet" }),
      el("span", { class: "statusbar-note", text: "Press Refresh (R) to run agent-hub status." })
    );
    return;
  }

  const { counts, checks, problems } = summarize(result);
  const clean = result.exit_code === 0 && !problems;
  const verdict = clean ? "Local files ready" : problems ? `${problems} problem${problems === 1 ? "" : "s"}` : `exit ${result.exit_code}`;
  const healthy = checks - problems;

  host.className = `statusbar ${clean ? "is-ok" : "is-bad"}`;
  host.title = Object.entries(counts)
    .map(([level, value]) => `${level}: ${value}`)
    .join("\n");

  host.append(
    el("span", { class: "statusbar-dot", "aria-hidden": "true" }),
    el("span", { class: "statusbar-verdict", text: verdict }),
    el("span", {
      class: "statusbar-note",
      text: `${healthy} of ${checks} check${checks === 1 ? "" : "s"} healthy${state?.machine_id ? ` on ${state.machine_id}` : ""}`,
    }),
  );

  for (const [level, label, toneClass] of PROBLEM_COUNTERS) {
    if (!counts[level]) continue;
    host.append(
      el(
        "button",
        {
          type: "button",
          class: `sb-count t-${toneClass}`,
          title: `Show ${counts[level]} × ${level}`,
          onClick: () => update({ filter: "problems" }),
        },
        [el("b", { text: String(counts[level]) }), el("span", { text: label })]
      )
    );
  }

  host.append(el("span", { class: "statusbar-meta", text: summaryMeta(counts, result) }));
}

function checkRow(line, withGroup) {
  const problem = isProblem(line.level);
  const toneName = tone(line.level);
  const glyph = problem || toneName === "warn" ? "alert" : line.level === "skip" ? "minus" : toneName === "info" ? "dot" : "check";
  return el("div", { class: `item t-${toneName}`, role: "listitem", title: line.text }, [
    el("span", { class: `ic t-${toneName}`, html: ICON(glyph) }),
    el("span", { class: "item-main" }, [
      el("span", { class: "row-label", text: line.label }),
      withGroup ? el("span", { class: "tag", text: line.group }) : null,
      el("span", { class: "row-detail", text: problem || !line.where ? line.detail : line.where }),
    ]),
    el("span", { class: `right lvl t-${toneName}`, text: LEVEL_WORD[line.level] || line.level }),
  ]);
}

function renderGroups(host, result, state, filter) {
  clear(host);
  if (!result) {
    host.append(
      el("div", { class: "empty" }, [
        el("strong", { text: "No status yet" }),
        "Press Refresh (R) to run agent-hub status on this machine.",
      ])
    );
    return;
  }

  const rows = (result.checks || []).filter((check) => check.level).map(statusRow);
  const groups = new Map();
  for (const line of rows) {
    if (!groups.has(line.group)) groups.set(line.group, []);
    groups.get(line.group).push(line);
  }

  const rank = groupOrder(state);
  const names = [...groups.keys()].sort((a, b) => {
    const [ga, ka] = rank(a);
    const [gb, kb] = rank(b);
    return ga - gb || ka.localeCompare(kb);
  });

  // "Needs attention" first, in group order; healthy rows stay grouped by agent.
  const attention = names.flatMap((name) => groups.get(name).filter((line) => isProblem(line.level)));
  if (attention.length) {
    host.append(el("section", { class: "check-sec" }, [
      el("h3", { class: "grp-label warn", text: "Needs attention" }),
      el("div", { class: "group", role: "list", "aria-label": "Needs attention" }, attention.map((line) => checkRow(line, true))),
    ]));
  }

  if (filter !== "problems") {
    const healthy = names
      .map((name) => [name, groups.get(name).filter((line) => !isProblem(line.level))])
      .filter(([, lines]) => lines.length);
    if (healthy.length) {
      const onlyOk = healthy.every(([, lines]) => lines.every((line) => line.level !== "skip" && tone(line.level) !== "warn"));
      host.append(el("section", { class: "check-sec" }, [
        el("h3", { class: "grp-label", text: onlyOk ? "Healthy" : "Other checks" }),
        ...healthy.map(([name, lines]) => {
          const kind = groupKind(name, state);
          return el("div", { class: "group", role: "list", "aria-label": name }, [
            el("div", { class: "group-head", "aria-hidden": "true" }, [
              el("span", { class: "group-name", text: name }),
              kind ? el("span", { class: "group-kind", text: kind }) : null,
              el("span", { class: "group-n", text: `${lines.length}` }),
            ]),
            ...lines.map((line) => checkRow(line, false)),
          ]);
        }),
      ]));
    }
  }

  if (!host.firstChild) {
    host.append(
      el("div", { class: "empty" }, [
        el("strong", { text: filter === "problems" ? "No problems" : "No output" }),
        filter === "problems" ? "Every target matches the repository. Switch to All to see the full check list." : "agent-hub status printed nothing.",
      ])
    );
  }
}

export function renderStatusView(store) {
  renderSummary($("#summary"), store.status, store.state);
  renderGroups($("#status-groups"), store.status, store.state, store.filter);

  // The All/Problems filter is only useful when there is something to filter to.
  const problems = store.status ? summarize(store.status).problems : 0;
  $("#status-toolbar").hidden = !problems;
  for (const button of $$("#status-filter button")) {
    const active = button.dataset.filter === store.filter;
    button.setAttribute("aria-pressed", String(active));
  }
}

export function renderLog(result) {
  const body = $("#log-body");
  const cmd = $("#log-cmd");
  const exit = $("#log-exit");
  const time = $("#log-time");

  if (!result) {
    cmd.textContent = "no command yet";
    exit.hidden = true;
    time.textContent = "";
    clear(body);
    $("#logbar").classList.remove("has-error", "has-ok");
    return;
  }

  cmd.textContent = `agent-hub ${result.display_command || result.command}`;
  exit.hidden = false;
  exit.textContent = `exit ${result.exit_code}`;
  exit.className = `log-exit ${result.exit_code === 0 ? "zero" : "nonzero"}`;
  time.textContent = result.at || formatTime();
  $("#logbar").classList.toggle("has-error", result.exit_code !== 0);
  $("#logbar").classList.toggle("has-ok", result.exit_code === 0);

  clear(body);
  const lines = result.lines || [];
  if (!lines.length) {
    body.append(el("span", { class: "log-line l-plain", text: "(no output)" }));
    return;
  }
  for (const line of lines) {
    body.append(
      el("span", { class: `log-line l-${tone(line.level)}` }, [
        el("span", { class: "lvl", text: line.level ? `[${line.level}]` : "" }),
        line.text,
      ])
    );
  }
  body.scrollTop = body.scrollHeight;
}
