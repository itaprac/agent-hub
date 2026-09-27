// Sidebar tree + editor shell shared by the Skills, Instructions and Config tabs.

import { clear, el, formatBytes, toast } from "./dom.js";
import { createEditor } from "./editor.js";
import { icon } from "./icons.js";

export function createWorkspace(section, { title, actions = [], buildTree, buildContext, onChanged, onDirty, emptyHint = "" }) {
  const expanded = new Set();
  let selected = null;
  let lastTree = [];
  let lastState = null;
  let query = "";
  let nodeDomSequence = 0;

  // Actions for the item behind the open file, such as Disable or Back up.
  const context = buildContext ? el("div", { class: "context-bar", role: "group" }) : null;

  const editor = createEditor({
    onSaved: async (path) => {
      selected = path;
      if (onChanged) await onChanged();
    },
    onDeleted: async () => {
      selected = null;
      if (onChanged) await onChanged();
    },
    onDirty,
    context,
    emptyTitle: "Pick a file",
    emptyBody: emptyHint || `Select a file in the ${title.toLowerCase()} tree to edit it here.`,
  });

  const treeId = `${section.id}-tree`;
  const tree = el("div", { class: "tree", id: treeId, "aria-label": `${title} files` });
  const search = el("input", {
    class: "search",
    type: "search",
    placeholder: `Filter ${title.toLowerCase()}`,
    autocomplete: "off",
    spellcheck: false,
    "aria-label": `Filter ${title.toLowerCase()}`,
    "aria-controls": treeId,
  });
  search.addEventListener("input", () => {
    query = search.value.trim().toLowerCase();
    paint(lastTree);
  });
  const count = el("span", { class: "sidebar-count" });
  const head = el("div", { class: "sidebar-head" }, [
    el("h1", { class: "sidebar-title", text: title }),
    count,
  ]);
  const actionBar = actions.length ? el("div", { class: "sidebar-actions" },
    actions.map((action, index) => el("button", {
      class: `btn btn-sm${index === 0 ? "" : " btn-ghost"}`,
      type: "button",
      title: action.title,
      onClick: action.run,
      html: `${action.icon ? icon(action.icon) : ""}<span>${action.label}</span>`,
    }))) : null;
  const searchWrap = el("label", { class: "search-wrap" }, [
    el("span", { class: "search-ico", "aria-hidden": "true", html: icon("search") }),
    search,
    el("kbd", { class: "search-kbd", "aria-hidden": "true", text: "/" }),
  ]);
  const sidebar = el("aside", { class: "sidebar", "aria-label": `${title} files` }, [head, actionBar, searchWrap, tree]);

  clear(section);
  section.append(sidebar, editor.element);

  async function select(file) {
    if (file.disabled) {
      toast(`${file.path} is not an editable text file`, "err", 3000);
      return;
    }
    const opened = await editor.open(file.path, { exists: file.exists, template: file.template || "" });
    if (opened) {
      selected = file.path;
      paint(lastTree);
      paintContext();
    }
  }

  // Open a path after it moved, for example into disabled/ and back.
  async function openPath(path) {
    await editor.close({ force: true });
    selected = null;
    if (path) {
      const opened = await editor.open(path);
      if (opened) selected = path;
    }
    paint(lastTree);
    paintContext();
  }

  function paintContext() {
    if (!context) return;
    const path = editor.path();
    const info = path && lastState ? buildContext(path, lastState, { editor, openPath }) : null;
    clear(context);
    context.hidden = !info;
    if (!info) return;
    context.setAttribute("aria-label", info.label);
    const parts = [
      el("span", { class: "context-copy" }, [
        el("span", { class: "context-line" }, [
          el("span", { class: "context-name", text: info.label }),
          info.off === undefined ? null : el("span", { class: `state-pill ${info.off ? "off" : "on"}`, text: info.off ? "Disabled" : "Enabled" }),
        ]),
        info.note ? el("span", { class: "context-note", text: info.note }) : null,
      ]),
      el("span", { class: "context-actions" }, info.actions.map((action) =>
        el("button", {
          type: "button",
          class: `btn btn-sm${action.kind ? ` btn-${action.kind}` : " btn-ghost"}`,
          text: action.label,
          title: action.title || "",
          onClick: action.run,
        })
      )),
    ];
    // DOM append() writes null as text, so drop the missing parts.
    context.append(...parts.filter(Boolean));
  }

  function fileItem(file) {
    const classes = ["tree-item", "tree-file"];
    if (!file.exists) classes.push("missing");
    if (file.off) classes.push("is-off");
    if (selected === file.path) classes.push("active");
    return el(
      "button",
      {
        class: classes.join(" "),
        title: file.path,
        "aria-pressed": String(selected === file.path),
        "aria-disabled": file.disabled ? "true" : null,
        onClick: () => select(file),
      },
      [
        el("span", { class: "fi", "aria-hidden": "true", html: icon(file.icon || "file") }),
        el("span", { class: "name", text: file.label }),
        el("span", { class: "meta" }, [
          ...(file.badges || []).map(badge),
          file.meta && !file.badges ? el("span", { class: "meta-text mono", text: file.meta }) : null,
        ]),
      ]
    );
  }

  function badge(item) {
    return el("span", { class: `tag${item.tone ? ` tag-${item.tone}` : ""}`, text: item.text, title: item.title || null });
  }

  function matchesQuery(text) {
    return !query || String(text || "").toLowerCase().includes(query);
  }

  function fileVisible(file) {
    return matchesQuery(file.label) || matchesQuery(file.path);
  }

  function nodeVisible(node) {
    return matchesQuery(node.label) || node.files.some(fileVisible);
  }

  function groupVisible(group) {
    if (!query) return true;
    if (matchesQuery(group.label)) return true;
    if (group.nodes?.some(nodeVisible)) return true;
    if (group.files?.some(fileVisible)) return true;
    return false;
  }

  function nodeItem(node) {
    const filtering = Boolean(query);
    const isOpen = filtering || expanded.has(node.id);
    const filesId = `${section.id}-node-files-${++nodeDomSequence}`;
    const wrapper = el("div", { class: `tree-node${isOpen ? " open" : ""}${node.off ? " is-off" : ""}` });
    const primary = node.files.find((file) => /^SKILL\.md$/i.test(file.label)) || node.files[0];
    const toggle = el(
      "button",
      {
        class: "caret-btn",
        title: filtering ? "Matching files are expanded while filtering" : isOpen ? "Collapse" : "Expand",
        "aria-label": `${isOpen ? "Collapse" : "Expand"} ${node.label}`,
        "aria-expanded": String(isOpen),
        "aria-controls": filesId,
        disabled: filtering,
        onClick: (event) => {
          event.stopPropagation();
          if (expanded.has(node.id)) expanded.delete(node.id);
          else expanded.add(node.id);
          paint(lastTree);
        },
      },
      [el("span", { class: "caret", "aria-hidden": "true", html: icon("chev") })]
    );
    const nameBtn = el(
      "button",
      {
        class: "tree-item tree-node-name",
        title: isOpen ? "Collapse" : primary ? `Open ${primary.path}` : node.title || node.label,
        "aria-pressed": String(Boolean(primary && selected === primary.path)),
        onClick: () => {
          if (filtering) {
            if (primary && !primary.disabled) select(primary);
            return;
          }
          if (expanded.has(node.id)) {
            expanded.delete(node.id);
          } else {
            expanded.add(node.id);
            if (primary && !primary.disabled) select(primary);
          }
          paint(lastTree);
        },
      },
      [
        el("span", { class: "fi", "aria-hidden": "true", html: icon("box") }),
        el("span", { class: "name", text: node.label }),
        el("span", { class: "meta" }, [
          ...(node.badges || []).map(badge),
          node.meta ? el("span", { class: "meta-text mono", text: node.meta, title: node.metaTitle || null }) : null,
        ]),
      ]
    );
    const files = filtering ? node.files.filter((file) => matchesQuery(node.label) || fileVisible(file)) : node.files;
    wrapper.append(
      el("div", { class: "tree-node-row" }, [toggle, nameBtn]),
      el("div", { class: "tree-files", id: filesId }, files.map(fileItem))
    );
    if (node.provenance) {
      const source = node.provenance.url
        ? el("a", { href: node.provenance.url, target: "_blank", rel: "noopener noreferrer", text: node.provenance.source })
        : el("span", { text: node.provenance.source });
      wrapper.append(el("dl", { class: "skill-provenance" }, [
        el("dt", { text: "Source" }), el("dd", {}, [source]),
        el("dt", { text: "Updated" }), el("dd", { text: node.provenance.updated }),
      ]));
    }
    return wrapper;
  }

  function paint(groups) {
    lastTree = groups;
    clear(tree);
    const visible = groups.filter(groupVisible);
    const total = groups.reduce((sum, group) => sum + (group.nodes || group.files || []).length, 0);
    count.textContent = total ? String(total) : "";
    if (!groups.length) {
      tree.append(el("div", { class: "tree-empty", text: "Nothing here yet." }));
      return;
    }
    if (!visible.length) {
      tree.append(el("div", { class: "tree-empty" }, [
        el("span", { class: "tree-empty-ico", "aria-hidden": "true", html: icon("search") }),
        el("strong", { text: "No matches" }),
        el("span", { text: query ? `Nothing matches “${search.value.trim()}”.` : "Nothing here yet." }),
      ]));
      return;
    }
    for (const group of visible) {
      const items = [];
      if (group.nodes) {
        const nodes = query ? group.nodes.filter(nodeVisible) : group.nodes;
        if (!nodes.length) items.push(el("div", { class: "tree-empty", text: group.emptyText || "no entries" }));
        else items.push(...nodes.map(nodeItem));
      }
      if (group.files) {
        const files = query && !matchesQuery(group.label) ? group.files.filter(fileVisible) : group.files;
        items.push(...files.map(fileItem));
      }
      tree.append(
        el("div", { class: "tree-group" }, [
          el("div", { class: "tree-group-head", title: group.title || "" }, [
            el("span", { class: "tree-group-ico", "aria-hidden": "true", html: icon(group.icon || "folder") }),
            el("span", { class: "tree-group-label", text: group.label }),
            group.note ? el("span", { class: "note", text: group.note }) : null,
            el("span", { class: "count", text: String(group.count ?? (group.nodes || group.files || []).length) }),
          ]),
          ...items,
        ])
      );
    }
  }

  return {
    editor,
    render(state) {
      lastState = state;
      paint(state ? buildTree(state) : []);
      paintContext();
    },
  };
}

// ---------------------------------------------------------------- tree builders

export function skillProvenance(skill) {
  if (!skill.installed) return null;
  const info = skill.provenance || {};
  let url = null;
  try {
    const parsed = new URL(info.source_url);
    if (["https:", "http:"].includes(parsed.protocol)) url = parsed.href;
  } catch { /* A source can be a repository name or a local path. */ }
  const date = info.updated_at || info.installed_at;
  const parsedDate = date ? new Date(date) : null;
  return {
    source: info.source || "Source not recorded",
    url,
    updated: parsedDate && !Number.isNaN(parsedDate.getTime()) ? parsedDate.toLocaleDateString() : "not recorded",
  };
}

function limitBadges(filter) {
  if (!filter) return [];
  const badges = [];
  if (filter.machines) badges.push({ text: `${filter.machines.join(", ")} only`, title: `Limited to Machines: ${filter.machines.join(", ")}` });
  if (filter.agents) badges.push({ text: `${filter.agents.join(", ")} only`, title: `Limited to Agents: ${filter.agents.join(", ")}` });
  return badges;
}

function skillNodes(skills, prefix, filters = {}) {
  return (skills || []).map((skill) => ({
    id: `${prefix}:${skill.name}`,
    label: skill.name,
    title: skill.path,
    off: Boolean(skill.disabled),
    provenance: skillProvenance(skill),
    meta: String(skill.files.length),
    metaTitle: `${skill.files.length} file${skill.files.length === 1 ? "" : "s"}`,
    badges: [
      skill.disabled ? { text: "Disabled", tone: "off" } : null,
      skill.installed ? { text: "Installed", tone: "ok", title: skill.provenance?.source || "Installed skill" } : null,
      ...limitBadges(filters[skill.name]),
    ].filter(Boolean),
    files: skill.files.map((file) => ({
      label: file.name,
      path: file.path,
      exists: true,
      disabled: !file.editable,
      icon: /\.(md|txt)$/i.test(file.name) ? "fileText" : "file",
      meta: file.editable ? formatBytes(file.size) : "binary",
    })),
  }));
}

export function buildSkillsTree(state) {
  const groups = [
    {
      label: "Global",
      icon: "globe",
      nodes: skillNodes(state.skills.global, "global", state.hub?.skills),
      emptyText: "no global skills",
    },
  ];
  for (const project of state.projects) {
    groups.push({
      label: project.name.replace(/--/g, "/"),
      icon: "git",
      note: project.available ? "" : "off-machine",
      title: project.path || project.note,
      nodes: skillNodes(state.skills.projects[project.name], `project:${project.name}`, state.hub?.skills),
      emptyText: "no project skills",
    });
  }
  return groups;
}

function instructionFiles(entries, backups) {
  return (entries || []).map((entry) => {
    const count = (backups?.[entry.source || entry.path] || []).length;
    const state = entry.disabled ? "disabled" : entry.exists ? entry.kind : "create";
    const words = { base: "Base", agent: "Overlay", disabled: "Disabled", create: "Create" };
    return {
      label: entry.name,
      path: entry.path,
      exists: entry.exists,
      off: Boolean(entry.disabled),
      icon: "fileText",
      meta: count ? `${state} · ${count} backup${count === 1 ? "" : "s"}` : state,
      badges: [
        { text: words[state] || state, tone: state === "disabled" ? "off" : state === "create" ? "accent" : "" },
        count ? { text: `${count} backup${count === 1 ? "" : "s"}` } : null,
      ].filter(Boolean),
    };
  });
}

// Entries without `source` come from an older server; they are never disabled.
const instructionSource = (entry) => entry.source || entry.path;

export function buildInstructionsTree(state) {
  const entries = state.instructions?.global || [];
  const backups = state.backups || {};
  return [
    { label: "Shared instructions", icon: "fileText", files: instructionFiles(entries.filter((entry) => instructionSource(entry) === "AGENTS.md"), backups) },
    { label: "Overlays", icon: "layers", files: instructionFiles(entries.filter((entry) => instructionSource(entry).startsWith("agents/")), backups) },
  ];
}

export function buildConfigTree(state) {
  return [
    {
      label: "Store",
      icon: "gear",
      files: (state.config_files || []).filter((file) => file.path === "hub.toml").map((file) => ({
        label: file.name,
        path: file.path,
        exists: file.exists,
        icon: "gear",
        meta: file.exists ? "" : "create",
        badges: file.exists ? [] : [{ text: "Create", tone: "accent" }],
      })),
    },
  ];
}
