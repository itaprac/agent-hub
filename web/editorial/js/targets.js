// Edit hub.toml settings in dialogs: Skill targets, and the Agents that Apply targets.

import { api } from "./api.js";
import { el } from "./dom.js";
import { choiceDialog } from "./modals.js";

let groupSequence = 0;

function agentInfo(state, id) {
  return (state.agents || []).find((agent) => agent.name === id) || { name: id, display_name: id, universal: false };
}

export function skillFilters(state, name) {
  return state.hub?.skills?.[name] || {};
}

export function targetSummary(state, name) {
  const filters = skillFilters(state, name);
  const machines = filters.machines ? `only ${filters.machines.join(", ")}` : "all Machines";
  const agents = filters.agents
    ? `only ${filters.agents.map((id) => agentInfo(state, id).display_name).join(", ")}`
    : "all Agents";
  return `${machines} · ${agents}`;
}

// "All" or "Only these" with one checkbox per option. `read()` returns null for all.
function choiceGroup({ legend, allLabel, options, selected, note }) {
  const id = `targets-${++groupSequence}`;
  const all = el("input", { type: "radio", name: id, checked: !selected });
  const some = el("input", { type: "radio", name: id, checked: Boolean(selected) });
  const boxes = options.map((option) => {
    const input = el("input", {
      type: "checkbox",
      value: option.value,
      checked: Boolean(option.locked) || (selected ? selected.includes(option.value) : false),
      disabled: !selected || Boolean(option.locked),
    });
    return {
      input,
      option,
      row: el("label", { class: `check-row${option.locked ? " locked" : ""}` }, [
        input,
        el("span", { class: "check-name", text: option.label }),
        option.detail ? el("span", { class: "check-detail", text: option.detail }) : null,
      ]),
    };
  });
  const sync = () => {
    for (const box of boxes) box.input.disabled = all.checked || Boolean(box.option.locked);
  };
  all.addEventListener("change", sync);
  some.addEventListener("change", () => {
    sync();
    if (!boxes.some((box) => box.input.checked && !box.option.locked)) {
      const first = boxes.find((box) => !box.option.locked);
      if (first) first.input.checked = true;
    }
  });
  const node = el("fieldset", { class: "check-group" }, [
    el("legend", { text: legend }),
    el("label", { class: "check-row" }, [all, el("span", { class: "check-name", text: allLabel })]),
    el("label", { class: "check-row" }, [some, el("span", { class: "check-name", text: "Only these:" })]),
    el("div", { class: "check-list" }, boxes.map((box) => box.row)),
    note ? el("div", { class: "hint", text: note }) : null,
  ]);
  return {
    node,
    read: () => (all.checked ? null : boxes.filter((box) => box.input.checked && !box.option.locked).map((box) => box.option.value)),
  };
}

export async function editSkillTargets(state, name, runHub) {
  const filters = skillFilters(state, name);
  const machineIds = new Set((state.machines || []).map((machine) => machine.machine));
  for (const id of filters.machines || []) machineIds.add(id);
  const machines = choiceGroup({
    legend: "Machines",
    allLabel: "All Machines",
    selected: filters.machines || null,
    options: [...machineIds].sort().map((id) => {
      const record = (state.machines || []).find((machine) => machine.machine === id);
      return { value: id, label: id, detail: record?.local ? "this Machine" : record ? "" : "no Machine record" };
    }),
  });

  // Agents on any Machine, plus the ones that the filter already names.
  const agentIds = new Set((state.agents || []).filter((agent) => agent.enabled).map((agent) => agent.name));
  for (const machine of state.machines || []) for (const id of machine.agents || []) agentIds.add(id);
  for (const id of filters.agents || []) agentIds.add(id);
  const universal = [...agentIds].filter((id) => agentInfo(state, id).universal);
  const agents = choiceGroup({
    legend: "Agents",
    allLabel: "All Agents",
    selected: filters.agents || null,
    options: [...agentIds].sort().map((id) => {
      const info = agentInfo(state, id);
      return { value: id, label: info.display_name || id, detail: info.universal ? "reads the Store directly" : "" };
    }),
    note: universal.length
      ? `${universal.map((id) => agentInfo(state, id).display_name).join(", ")} read${universal.length === 1 ? "s" : ""} ~/.agents/skills directly, so this filter cannot hide a global Skill from ${universal.length === 1 ? "it" : "them"}.`
      : "",
  });

  const choice = await choiceDialog({
    title: `Targets for ${name}`,
    sub: "Saved in hub.toml. Apply runs on this Machine; Sync takes the change to the other Machines.",
    wide: true,
    body: () => [el("div", { class: "targets-grid" }, [machines.node, agents.node])],
    buttons: [
      { label: "Cancel", value: null },
      { label: "Save targets", value: "save", kind: "primary" },
    ],
  });
  if (choice !== "save") return null;
  return runHub(`targets for ${name}`, () => api.skillTargets(name, machines.read(), agents.read()));
}

export async function editAgents(state, runHub) {
  const enabled = state.hub?.enabled ?? null;
  const agentIds = new Set((state.agents || []).filter((agent) => agent.detected).map((agent) => agent.name));
  for (const id of enabled || []) agentIds.add(id);
  const agents = choiceGroup({
    legend: "Agents that Apply targets on every Machine",
    allLabel: "Detected Agents (default)",
    selected: enabled,
    options: [...agentIds].sort().map((id) => {
      const info = agentInfo(state, id);
      return { value: id, label: info.display_name || id, detail: info.detected ? "detected here" : "not detected here" };
    }),
    note: "The list shows Agents detected on this Machine. To add another Agent, edit hub.toml.",
  });
  const mode = el("select", { id: "agents-mode" }, [
    el("option", { value: "symlink", text: "symlink (recommended)", selected: state.hub?.mode !== "copy" }),
    el("option", { value: "copy", text: "copy", selected: state.hub?.mode === "copy" }),
  ]);
  const choice = await choiceDialog({
    title: "Agents and mode",
    sub: "Saved in hub.toml. Apply runs on this Machine; Sync takes the change to the other Machines.",
    wide: true,
    body: () => [
      agents.node,
      el("div", { class: "field" }, [
        el("label", { for: "agents-mode", text: "How Skills reach Agents" }),
        mode,
        el("div", { class: "hint", text: "Copy is for systems without symbolic links, such as Windows." }),
      ]),
    ],
    buttons: [
      { label: "Cancel", value: null },
      { label: "Save", value: "save", kind: "primary" },
    ],
  });
  if (choice !== "save") return null;
  return runHub("agents and mode", () => api.agentSettings(agents.read(), mode.value));
}

export async function showSkillTargets(state, runHub) {
  const names = new Set((state.skills?.global || []).map((skill) => skill.name));
  for (const skills of Object.values(state.skills?.projects || {})) for (const skill of skills) names.add(skill.name);
  for (const name of Object.keys(state.hub?.skills || {})) names.add(name);
  const choice = await choiceDialog({
    title: "Skill targets",
    sub: "Which Machines and Agents get each Skill. A Skill without a row in hub.toml goes everywhere.",
    wide: true,
    body: (choose) => [
      el("div", { class: "backup-list", role: "list" }, [...names].sort((a, b) => a.localeCompare(b)).map((name) =>
        el("div", { class: "target-row", role: "listitem" }, [
          el("span", { class: "backup-date", text: name }),
          el("span", { class: `target-summary${state.hub?.skills?.[name] ? " filtered" : ""}`, text: targetSummary(state, name) }),
          el("button", { type: "button", class: "btn btn-sm", text: "Edit", "aria-label": `Edit targets for ${name}`, onClick: () => choose(name) }),
        ]))),
    ],
    buttons: [{ label: "Close", value: null }],
  });
  if (choice) return editSkillTargets(state, choice, runHub);
  return null;
}
