// Status identity comes from check metadata; command output stays verbatim.
import assert from "node:assert/strict";
import { renderLog, renderStatusView, summarize } from "../web/js/status.js";

class StatusNode {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.className = "";
    this.textContent = "";
    this.classList = { toggle() {}, remove() {} };
  }
  append(...children) { this.children.push(...children); }
  get firstChild() { return this.children[0]; }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); }
  setAttribute(name, value) { this[name] = value; }
  addEventListener() {}
}
const previousDocument = globalThis.document;
const previousNode = globalThis.Node;
const nodes = new Map([
  "summary", "status-groups", "status-toolbar", "log-body", "log-cmd", "log-exit", "log-time", "logbar",
].map((id) => [`#${id}`, new StatusNode("div")]));
globalThis.Node = StatusNode;
globalThis.document = {
  querySelector: (selector) => nodes.get(selector),
  querySelectorAll: () => [],
  createElement: (tag) => new StatusNode(tag),
  createTextNode: (text) => Object.assign(new StatusNode("text"), { textContent: text }),
};
const descendants = (node) => [node, ...node.children.flatMap(descendants)];
const byClass = (node, name) => descendants(node).filter((child) => child.className === name);
const labels = () => nodes.get("#status-groups").children.map((group) => [
  byClass(group, "group-name")[0]?.textContent,
  byClass(group, "row-label").map((node) => node.textContent),
]);
const checks = [
  { kind: "git", level: "ok", text: "Store has no changes", target: "/store" },
  { kind: "project", level: "skip", project: "absent", text: "Checkout is unavailable" },
  { kind: "skill", level: "DRIFT", agent: "codex", project: "demo", name: "review", text: "replace old link: /target", target: "/target" },
  { kind: "instruction", level: "ok", agent: "codex", text: "Managed block matches", target: "/instructions" },
  { kind: "skill", level: "MISSING", project: "demo", name: "shared", text: "Shared Skill is missing" },
  { kind: "orphan", level: "STALE", text: "An unused link remains", target: "/old" },
  { kind: "git", level: "", text: "raw command output" },
];
const result = {
  command: "status", exit_code: 1, at: "12:00", checks,
  lines: [{ level: "ERROR", text: "log-only error" }, { level: "", text: "raw output: keep this" }],
};
const view = { status: result, state: { agents: [{ name: "codex", mode: "symlink" }] }, filter: "all" };
try {
  assert.deepEqual(summarize(result), {
    counts: { ok: 2, skip: 1, DRIFT: 1, MISSING: 1, STALE: 1 }, checks: 6, problems: 3, actions: 0,
  });
  renderStatusView(view);
  const expectedLabels = [
    ["codex", ["project demo/review", "global"]],
    ["orphan", ["orphan"]],
    ["projects", ["project absent", "project demo/shared"]],
    ["git", ["git"]],
  ];
  assert.deepEqual(labels(), expectedLabels);
  assert.equal(nodes.get("#status-toolbar").hidden, false);
  assert.ok(byClass(nodes.get("#status-groups"), "row-detail").some((node) => node.textContent === "replace old link: /target"));

  renderStatusView({ ...view, status: { ...result, checks: checks.map((check) => ({ ...check, text: "totally different: prose" })) } });
  assert.deepEqual(labels(), expectedLabels);
  renderStatusView({ ...view, filter: "problems" });
  assert.deepEqual(labels(), [
    ["codex", ["project demo/review"]], ["orphan", ["orphan"]], ["projects", ["project demo/shared"]],
  ]);
  renderLog(result);
  const logText = descendants(nodes.get("#log-body")).map((node) => node.textContent).filter(Boolean);
  assert.deepEqual(logText, ["[ERROR]", "log-only error", "raw output: keep this"]);

  renderStatusView({ ...view, status: { ...result, exit_code: 0, checks: [] } });
  assert.equal(nodes.get("#status-toolbar").hidden, true);
  assert.equal(byClass(nodes.get("#summary"), "statusbar-verdict")[0].textContent, "Clean");
} finally {
  if (previousDocument === undefined) delete globalThis.document; else globalThis.document = previousDocument;
  if (previousNode === undefined) delete globalThis.Node; else globalThis.Node = previousNode;
}
console.log("WEB STATUS TEST PASSED");
