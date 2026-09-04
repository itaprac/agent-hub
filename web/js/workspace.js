// Sidebar tree + editor shell shared by the Skills, Instructions and Config tabs.

import { clear, el, formatBytes, toast } from "./dom.js";
import { createEditor } from "./editor.js";

export function createWorkspace(section, { title, actions = [], buildTree, onChanged, onDirty }) {
  const expanded = new Set();
  let selected = null;
  let lastTree = [];
  let query = "";
  let nodeDomSequence = 0;

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
    emptyTitle: "Pick a file",
    emptyBody: `Select a file in the ${title.toLowerCase()} tree.`,
  });

  const treeId = `${section.id}-tree`;
  const tree = el("div", { class: "tree", id: treeId, "aria-label": `${title} files` });
  const search = el("input", {
    class: "search",
    type: "search",
    placeholder: "Filter  /",
    autocomplete: "off",
    spellcheck: false,
    "aria-label": `Filter ${title.toLowerCase()}`,
    "aria-controls": treeId,
  });
  search.addEventListener("input", () => {
    query = search.value.trim().toLowerCase();
    paint(lastTree);
  });
  const head = el("div", { class: "sidebar-head" }, [
    el("span", { class: "sidebar-title", text: title }),
    el("span", { class: "spacer" }),

  ]);
  const actionBar = actions.length ? el("div", { class: "sidebar-actions" },
    actions.map((action) => el("button", { class: "btn", text: action.label, title: action.title, onClick: action.run }))) : null;
  const sidebar = el("aside", { class: "sidebar" }, [head, actionBar, search, tree]);

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
    }
  }

  function fileItem(file) {
    const classes = ["tree-item", "tree-file"];
    if (!file.exists) classes.push("missing");
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
        el("span", { class: "name", text: file.label }),
        file.meta ? el("span", { class: "meta", text: file.meta }) : null,
      ]
    );
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
    const wrapper = el("div", { class: `tree-node${isOpen ? " open" : ""}` });
    const primary = node.files.find((file) => /^SKILL\.md$/i.test(file.label)) || node.files[0];
    const toggle = el(
      "button",
      {
        class: "caret-btn",
        title: filtering ? "Matching files are expanded while filtering" : isOpen ? "Collapse" : "Expand",
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
      [el("span", { class: "caret", text: "▸" })]
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
        el("span", { class: "name", text: node.label }),
        node.meta ? el("span", { class: "meta", text: node.meta }) : null,
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
      wrapper.append(el("div", { class: "skill-provenance" }, [
        el("span", { class: "skill-installed", text: "Installed" }), source,
        el("span", { text: `Updated ${node.provenance.updated}` }),
      ]));
    }
    return wrapper;
  }

  function paint(groups) {
    lastTree = groups;
    clear(tree);
    const visible = groups.filter(groupVisible);
    if (!groups.length) {
      tree.append(el("div", { class: "tree-empty", text: "nothing here yet" }));
      return;
    }
    if (!visible.length) {
      tree.append(el("div", { class: "tree-empty", text: query ? `no matches for "${search.value.trim()}"` : "nothing here yet" }));
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
            el("span", { text: group.label }),
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
      paint(state ? buildTree(state) : []);
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

function skillNodes(skills, prefix) {
  return (skills || []).map((skill) => ({
    id: `${prefix}:${skill.name}`,
    label: skill.name,
    title: skill.path,
    provenance: skillProvenance(skill),
    meta: `${skill.files.length} file${skill.files.length === 1 ? "" : "s"}`,
    files: skill.files.map((file) => ({
      label: file.name,
      path: file.path,
      exists: true,
      disabled: !file.editable,
      meta: file.editable ? formatBytes(file.size) : "binary",
    })),
  }));
}

export function buildSkillsTree(state) {
  const groups = [
    {
      label: "Global",
      nodes: skillNodes(state.skills.global, "global"),
      emptyText: "no global skills",
    },
  ];
  for (const project of state.projects) {
    groups.push({
      label: project.name,
      note: project.available ? "" : "off-machine",
      title: project.path || project.note,
      nodes: skillNodes(state.skills.projects[project.name], `project:${project.name}`),
      emptyText: "no project skills",
    });
  }
  return groups;
}

function instructionFiles(entries) {
  return (entries || []).map((entry) => ({
    label: entry.name,
    path: entry.path,
    exists: entry.exists,
    meta: entry.exists ? entry.kind : "create",
  }));
}

export function buildInstructionsTree(state) {
  const entries = state.instructions?.global || [];
  return [
    { label: "Shared instructions", files: instructionFiles(entries.filter((entry) => entry.path === "AGENTS.md")) },
    { label: "Overlays", files: instructionFiles(entries.filter((entry) => entry.path.startsWith("agents/"))) },
  ];
}

export function buildConfigTree(state) {
  return [
    {
      label: "Store",
      files: (state.config_files || []).filter((file) => file.path === "hub.toml").map((file) => ({
        label: file.name,
        path: file.path,
        exists: file.exists,
        meta: file.exists ? "" : "create",
      })),
    },
  ];
}
