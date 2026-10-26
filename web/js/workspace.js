// Skills, Instructions and Config pages: a card grid or grouped list of items,
// and a right-side sheet with the item's files, its actions and the editor.

import { api } from "./api.js";
import { $, clear, el, formatBytes, toast } from "./dom.js";
import { createEditor } from "./editor.js";

const icon = (name) => `<svg class="i" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

// Skill descriptions come from SKILL.md frontmatter; cached by path and size.
const descriptions = new Map();

export function parseDescription(content) {
  const text = String(content || "");
  const front = /^---\r?\n([\s\S]*?)\r?\n---/.exec(text);
  if (front) {
    const lines = front[1].split(/\r?\n/);
    const index = lines.findIndex((line) => /^description\s*:/.test(line));
    if (index >= 0) {
      let value = lines[index].replace(/^description\s*:\s*/, "");
      const block = /^[>|][-+]?\s*$/.test(value);
      const parts = block ? [] : [value];
      for (const line of lines.slice(index + 1)) {
        if (!/^\s+\S/.test(line)) break;
        parts.push(line.trim());
      }
      value = parts.join(" ").trim();
      value = value.replace(/^(["'])([\s\S]*)\1$/, "$2");
      if (value) return value;
    }
  }
  const body = front ? text.slice(front[0].length) : text;
  const line = body.split(/\r?\n/).map((item) => item.trim())
    .find((item) => item && !/^(#|```|~~~|<!--|---|>|\||-\s*$)/.test(item));
  return line ? line.replace(/[*_`]/g, "") : "";
}

export function createWorkspace(section, {
  title, eyebrow = "Repository", lede = () => "", layout = "list", itemIcon = "doc",
  actions = [], buildTree, buildContext, onChanged, onDirty,
}) {
  let lastTree = [];
  let lastState = null;
  let lastStatus = null;
  let query = "";
  let active = false;
  let openItem = null; // { kind: "node" | "file", id }
  let opener = null;
  let sheetOpen = false;
  let describing = false;

  const editor = createEditor({
    onSaved: async () => {
      if (onChanged) await onChanged();
    },
    onDeleted: async () => {
      // A single-file item has nothing left to show once its file is gone.
      if (openItem?.kind === "file") closeSheet();
      else paintSheet();
      if (onChanged) await onChanged();
    },
    onDirty: (dirty) => {
      if (sheetOpen) paintFileList();
      if (onDirty) onDirty(dirty);
    },
    emptyTitle: "Pick a file",
    emptyBody: "Choose one of this item’s files.",
  });

  // ---------------------------------------------------------------- page
  const ledeNode = el("p", { class: "lede" });
  const bodyId = `${section.id}-items`;
  const search = el("input", {
    class: "search",
    type: "search",
    placeholder: `Filter ${title.toLowerCase()}`,
    autocomplete: "off",
    spellcheck: false,
    "aria-label": `Filter ${title.toLowerCase()}`,
    "aria-controls": bodyId,
    "aria-keyshortcuts": "/",
  });
  search.addEventListener("input", () => {
    query = search.value.trim().toLowerCase();
    paint();
  });
  const toolbar = el("div", { class: "toolbar" }, [
    el("label", { class: "search-box" }, [el("span", { class: "search-ic", html: icon("search") }), search, el("kbd", { text: "/", "aria-hidden": "true" })]),
    el("span", { class: "spacer" }),
    ...actions.map((action) => el("button", {
      type: "button",
      class: `btn${action.kind ? ` ${action.kind}` : ""}`,
      title: action.title,
      onClick: action.run,
    }, [action.icon ? el("span", { class: "btn-ic", html: icon(action.icon) }) : null, action.label])),
  ]);
  const itemsHost = el("div", { class: `ws-items ws-${layout}`, id: bodyId });
  clear(section);
  section.append(
    el("header", { class: "page-head" }, [
      el("p", { class: "eyebrow", text: eyebrow }),
      el("h1", { text: title }),
      ledeNode,
    ]),
    toolbar,
    itemsHost,
  );

  // ---------------------------------------------------------------- sheet
  const sheetId = `${section.id}-sheet`;
  const sheetTitle = el("h2", { id: `${sheetId}-title` });
  const crumbs = el("div", { class: "crumbs" });
  const sheetActs = el("div", { class: "sheet-acts", role: "group" });
  const closeButton = el("button", {
    type: "button", class: "iconbtn", "aria-label": "Close", title: "Close (Esc)", html: icon("x"),
    onClick: () => closeSheet(),
  });
  const fileList = el("nav", { class: "filelist", "aria-label": "Files" });
  const sheet = el("div", {
    class: "sheet", id: sheetId, role: "dialog", "aria-modal": "true",
    "aria-labelledby": sheetTitle.id, "aria-hidden": "true", inert: true,
  }, [
    el("div", { class: "sheet-head" }, [
      el("div", { class: "sheet-id" }, [sheetTitle, crumbs]),
      closeButton,
    ]),
    sheetActs,
    el("div", { class: "sheet-main" }, [fileList, editor.element]),
  ]);
  const scrim = el("div", { class: "scrim", "aria-hidden": "true", onClick: () => closeSheet() });
  document.body.append(scrim, sheet);

  function setBackgroundInert(value) {
    for (const node of [$(".side"), $("#main"), $("#logbar")]) {
      if (node) node.inert = value;
    }
  }

  function showSheet(trigger) {
    opener = trigger || document.activeElement;
    if (!sheetOpen) {
      sheetOpen = true;
      sheet.inert = false;
      sheet.setAttribute("aria-hidden", "false");
      sheet.classList.add("on");
      scrim.classList.add("on");
      setBackgroundInert(true);
    }
  }

  function closeSheet({ restoreFocus = true } = {}) {
    if (!sheetOpen) return false;
    sheetOpen = false;
    sheet.classList.remove("on");
    scrim.classList.remove("on");
    setBackgroundInert(false);
    paint();
    // Move focus out before hiding the sheet from assistive technology.
    const target = openItem && itemsHost.querySelector(`[data-id="${CSS.escape(openItem.id)}"]`);
    const focusable = opener && opener.isConnected && !opener.closest(".sheet") ? opener : target;
    if (restoreFocus && focusable) focusable.focus({ preventScroll: false });
    else if (sheet.contains(document.activeElement)) document.activeElement.blur();
    sheet.setAttribute("aria-hidden", "true");
    sheet.inert = true;
    return true;
  }

  function findNode(id) {
    for (const group of lastTree) {
      const node = (group.nodes || []).find((item) => item.id === id);
      if (node) return { node, group };
    }
    return null;
  }

  function findFile(path) {
    for (const group of lastTree) {
      const file = (group.files || []).find((item) => item.path === path);
      if (file) return { file, group };
    }
    return null;
  }

  async function select(file) {
    if (file.disabled) {
      toast(`${file.path} is not an editable text file`, "err", 3000);
      return false;
    }
    const opened = await editor.open(file.path, { exists: file.exists, template: file.template || "" });
    paintSheet();
    return opened;
  }

  async function openNode(node, trigger) {
    const current = editor.path();
    const primary = node.files.find((file) => /^SKILL\.md$/i.test(file.label) && !file.disabled)
      || node.files.find((file) => !file.disabled);
    const keep = current && node.files.some((file) => file.path === current);
    if (!keep && primary && editor.isDirty() && !(await editor.confirmDiscard())) return;
    openItem = { kind: "node", id: node.id };
    showSheet(trigger);
    if (!keep && primary) {
      if (editor.isDirty()) await editor.close({ force: true });
      await select(primary);
    } else if (!keep) {
      await editor.close({ force: true });
    }
    paintSheet();
    paint();
    editor.focus();
  }

  async function openFile(file, trigger) {
    if (file.disabled) {
      toast(`${file.path} is not an editable text file`, "err", 3000);
      return;
    }
    if (editor.path() !== file.path && editor.isDirty() && !(await editor.confirmDiscard())) return;
    openItem = { kind: "file", id: file.path };
    showSheet(trigger);
    if (editor.path() !== file.path) {
      if (editor.isDirty()) await editor.close({ force: true });
      await select(file);
    }
    paintSheet();
    paint();
    editor.focus();
  }

  // Open a path after it moved, for example into disabled/ and back.
  async function openPath(path) {
    await editor.close({ force: true });
    if (path) {
      await editor.open(path);
      if (openItem?.kind === "file") openItem = { kind: "file", id: path };
    } else {
      closeSheet();
    }
    paintSheet();
    paint();
  }

  function paintFileList() {
    const found = openItem?.kind === "node" ? findNode(openItem.id) : null;
    // The Skill's SKILL.md leads, then the rest in Store order.
    const files = [...(found?.node.files || [])].sort((a, b) => Number(/^SKILL\.md$/i.test(b.label)) - Number(/^SKILL\.md$/i.test(a.label)));
    clear(fileList);
    fileList.hidden = files.length < 2;
    sheet.classList.toggle("has-files", files.length > 1);
    if (files.length < 2) return;
    fileList.append(el("p", { class: "filelist-head", text: plural(files.length, "file") }));
    const current = editor.path();
    let lastDir = "";
    for (const file of files) {
      const on = file.path === current;
      const cut = file.label.lastIndexOf("/");
      const dir = cut < 0 ? "" : file.label.slice(0, cut + 1);
      if (dir && dir !== lastDir) fileList.append(el("p", { class: "filelist-dir mono", text: dir }));
      lastDir = dir;
      fileList.append(el("button", {
        type: "button",
        class: `file${on ? " on" : ""}${file.disabled ? " is-binary" : ""}`,
        title: file.path,
        "aria-current": on ? "true" : null,
        "aria-disabled": file.disabled ? "true" : null,
        onClick: () => select(file),
      }, [
        el("span", { class: "file-name", text: cut < 0 ? file.label : file.label.slice(cut + 1) }),
        el("span", { class: "file-meta", text: on && editor.isDirty() ? "edited" : file.meta || "" }),
      ]));
    }
    fileList.querySelector(".file.on")?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }

  function paintSheet() {
    if (!openItem) return;
    const nodeHit = openItem.kind === "node" ? findNode(openItem.id) : null;
    const fileHit = openItem.kind === "file" ? findFile(openItem.id) : null;
    const path = editor.path();
    const info = path && lastState && buildContext ? buildContext(path, lastState, { editor, openPath }) : null;
    const node = nodeHit?.node;
    const group = nodeHit?.group || fileHit?.group;
    sheetTitle.textContent = node?.label || info?.label || fileHit?.file.label || (path ? path.split("/").pop() : title);

    clear(crumbs);
    const parts = [];
    if (info && info.off !== undefined) {
      parts.push(el("span", { class: `state-pill ${info.off ? "off" : "on"}`, text: info.off ? "Disabled" : "Enabled" }));
    }
    if (group?.label && !info?.note) parts.push(el("span", { text: group.label }));
    if (info?.note) parts.push(el("span", { text: info.note }));
    if (node?.provenance) {
      const source = node.provenance.url
        ? el("a", { href: node.provenance.url, target: "_blank", rel: "noopener noreferrer", text: node.provenance.source })
        : el("span", { class: "mono", text: node.provenance.source });
      parts.push(el("span", { class: "prov" }, ["Installed from ", source]));
      parts.push(el("span", { text: `Updated ${node.provenance.updated}` }));
    }
    crumbs.append(...parts);

    clear(sheetActs);
    const acts = info?.actions || [];
    sheetActs.hidden = !acts.length;
    sheetActs.setAttribute("aria-label", info ? `${info.label} actions` : "Actions");
    for (const action of acts) {
      sheetActs.append(el("button", {
        type: "button",
        class: `btn sm${action.kind === "danger" ? " ghost danger" : action.kind === "primary" ? " primary" : ""}`,
        text: action.label,
        title: action.title || "",
        onClick: action.run,
      }));
    }
    paintFileList();
  }

  // ---------------------------------------------------------------- items
  const matches = (text) => !query || String(text || "").toLowerCase().includes(query);
  const fileVisible = (file) => matches(file.label) || matches(file.path);
  const nodeVisible = (node) => matches(node.label) || matches(descriptions.get(node.descKey)?.text)
    || node.files.some(fileVisible);

  function badge(item) {
    return el("span", { class: `badge${item.tone ? ` ${item.tone}` : ""}`, text: item.text, title: item.title || null });
  }

  function card(node) {
    const desc = descriptions.get(node.descKey);
    const on = sheetOpen && openItem?.id === node.id;
    return el("button", {
      type: "button",
      class: `card${node.off ? " is-off" : ""}${on ? " sel" : ""}`,
      dataset: { id: node.id },
      title: node.title || node.label,
      "aria-haspopup": "dialog",
      onClick: (event) => openNode(node, event.currentTarget),
    }, [
      el("span", { class: "card-title" }, [
        el("span", { class: "skic", html: icon(itemIcon) }),
        el("span", { class: "card-name", text: node.label }),
      ]),
      el("span", { class: `card-desc${desc?.text ? "" : " is-path"}`, text: desc?.text || node.title || "" }),
      el("span", { class: "card-foot" }, [
        ...(node.badges || []).map(badge),
        el("span", { class: "files", text: plural(node.files.length, "file") }),
      ]),
    ]);
  }

  function row(file) {
    const [dir] = file.path.includes("/") ? [file.path.slice(0, file.path.lastIndexOf("/") + 1)] : [""];
    const on = sheetOpen && openItem?.id === file.path;
    return el("button", {
      type: "button",
      class: `item clickable${file.off ? " is-off" : ""}${file.exists === false ? " is-missing" : ""}${on ? " sel" : ""}`,
      dataset: { id: file.path },
      title: file.path,
      "aria-haspopup": "dialog",
      "aria-disabled": file.disabled ? "true" : null,
      onClick: (event) => openFile(file, event.currentTarget),
    }, [
      el("span", { class: "ic neutral", html: icon(itemIcon) }),
      el("span", { class: "item-main" }, [
        el("span", { class: "item-name", text: file.label }),
        dir ? el("span", { class: "path mono", text: dir }) : null,
      ]),
      el("span", { class: "right" }, [
        ...(file.badges || []).map(badge),
        file.meta ? el("span", { text: file.meta }) : null,
        el("span", { class: "chev", html: icon("chev") }),
      ]),
    ]);
  }

  function paint() {
    const groups = lastTree;
    clear(itemsHost);
    ledeNode.textContent = lastState ? lede(lastState, groups) : "";
    let shown = 0;
    for (const group of groups) {
      const nodes = (group.nodes || []).filter(nodeVisible);
      const files = (group.files || []).filter((file) => matches(group.label) || fileVisible(file));
      const total = (group.nodes || group.files || []).length;
      if (query && !nodes.length && !files.length) continue;
      shown += nodes.length + files.length;
      if (group.nodes) {
        itemsHost.append(el("section", { class: "ws-group", "aria-label": group.label }, [
          el("div", { class: "sec-head" }, [
            el("h2", { class: "h-sm", title: group.title || null, text: group.label }),
            el("p", { text: [group.note, plural(total, layout === "cards" ? "skill" : "item")].filter(Boolean).join(" · ") }),
          ]),
          nodes.length
            ? el("div", { class: "cards" }, nodes.map(card))
            : el("p", { class: "empty-line", text: group.emptyText || "Nothing here yet." }),
        ]));
      } else {
        itemsHost.append(el("section", { class: "ws-group", "aria-label": group.label }, [
          el("p", { class: "grp-label", text: group.label }),
          files.length
            ? el("div", { class: "group" }, files.map(row))
            : el("p", { class: "empty-line", text: group.emptyText || "Nothing here yet." }),
        ]));
      }
    }
    if (!groups.length || (query && !shown)) {
      itemsHost.append(el("div", { class: "empty" }, [
        el("strong", { text: query ? `No ${title.toLowerCase()} match “${search.value.trim()}”` : "Nothing here yet" }),
        el("span", { text: query ? "Try a different name, or clear the filter." : `This Store has no ${title.toLowerCase()}.` }),
      ]));
    }
    describe();
  }

  // Read SKILL.md descriptions for visible cards, one request at a time.
  async function describe() {
    if (!active || describing || layout !== "cards") return;
    const pending = lastTree.flatMap((group) => group.nodes || []).filter((node) => node.descKey && !descriptions.has(node.descKey));
    if (!pending.length) return;
    describing = true;
    try {
      for (const node of pending) {
        if (descriptions.has(node.descKey)) continue;
        try {
          const file = await api.readFile(node.descPath);
          descriptions.set(node.descKey, { text: parseDescription(file.content) });
        } catch {
          descriptions.set(node.descKey, { text: "" });
        }
        const target = itemsHost.querySelector(`[data-id="${CSS.escape(node.id)}"] .card-desc`);
        const text = descriptions.get(node.descKey).text;
        if (target && text) {
          target.textContent = text;
          target.classList.remove("is-path");
        }
      }
    } finally {
      describing = false;
    }
  }

  return {
    editor,
    render(state, status) {
      lastState = state;
      lastStatus = status;
      lastTree = state ? buildTree(state, lastStatus) : [];
      paint();
      if (sheetOpen) paintSheet();
    },
    activate() {
      active = true;
      describe();
    },
    deactivate() {
      active = false;
      closeSheet({ restoreFocus: false });
    },
    isSheetOpen: () => sheetOpen,
    closeSheet,
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

const PROBLEM_LEVELS = ["DRIFT", "MISSING", "STALE", "ERROR", "CONFLICT"];
const levelWord = (level) => level[0] + level.slice(1).toLowerCase();

function limitText(state, filters) {
  if (!filters) return "";
  const names = [];
  if (filters.machines) names.push(...filters.machines);
  if (filters.agents) {
    names.push(...filters.agents.map((id) => (state.agents || []).find((agent) => agent.name === id)?.display_name || id));
  }
  return names.length ? `Only ${names.join(", ")}` : "";
}

function skillNodes(skills, prefix, state, status) {
  const filters = state.hub?.skills || {};
  const checks = status?.checks || [];
  return (skills || []).map((skill) => {
    const primary = skill.files.find((file) => /^SKILL\.md$/i.test(file.name) && file.editable);
    const problems = [...new Set(checks
      .filter((check) => check.name === skill.name && PROBLEM_LEVELS.includes(check.level))
      .map((check) => check.level))];
    const limited = limitText(state, filters[skill.name]);
    const provenance = skillProvenance(skill);
    return {
      id: `${prefix}:${skill.name}`,
      label: skill.name,
      title: skill.path,
      off: Boolean(skill.disabled),
      provenance,
      descPath: primary?.path || null,
      descKey: primary ? `${primary.path}:${primary.size}` : null,
      badges: [
        ...problems.map((level) => ({ text: levelWord(level), tone: "warn", title: `agent-hub status reports ${level}` })),
        skill.disabled ? { text: "Disabled", tone: "off" } : null,
        limited && !skill.disabled ? { text: limited, title: "Limited by Skill targets in hub.toml" } : null,
        provenance ? { text: "Installed", title: `Installed from ${provenance.source}` } : null,
      ].filter(Boolean),
      meta: skill.disabled
        ? "disabled"
        : `${filters[skill.name] ? "limited · " : ""}${skill.files.length} file${skill.files.length === 1 ? "" : "s"}`,
      files: skill.files.map((file) => ({
        label: file.name,
        path: file.path,
        exists: true,
        disabled: !file.editable,
        meta: file.editable ? formatBytes(file.size) : "binary",
      })),
    };
  });
}

export function projectLabel(name) {
  return String(name || "").replace(/--/g, "/");
}

export function buildSkillsTree(state, status = null) {
  const groups = [
    {
      label: "Global",
      nodes: skillNodes(state.skills.global, "global", state, status),
      emptyText: "No global skills yet.",
    },
  ];
  for (const project of state.projects) {
    groups.push({
      label: projectLabel(project.name),
      note: project.available ? "" : "off-machine",
      title: project.path || project.note,
      nodes: skillNodes(state.skills.projects[project.name], `project:${project.name}`, state, status),
      emptyText: "No project skills.",
    });
  }
  return groups;
}

function overlayTarget(state, entry) {
  const agent = (state?.agents || []).find((item) => `${item.name}.md` === entry.name);
  const target = agent?.keys?.instructions_global;
  return target ? target.split("/").pop() : "";
}

function instructionFiles(entries, backups, state) {
  return (entries || []).map((entry) => {
    const count = (backups?.[entry.source || entry.path] || []).length;
    const kind = entry.kind === "base" ? "Base" : overlayTarget(state, entry) ? `Appended to ${overlayTarget(state, entry)}` : "Overlay";
    const words = entry.disabled ? "disabled" : entry.exists ? kind : "create";
    return {
      label: entry.name,
      path: entry.path,
      exists: entry.exists,
      off: Boolean(entry.disabled),
      badges: entry.disabled ? [{ text: "Disabled", tone: "off" }] : !entry.exists ? [{ text: "Not created" }] : [],
      meta: count ? `${words} · ${count} backup${count === 1 ? "" : "s"}` : words,
    };
  });
}

// Entries without `source` come from an older server; they are never disabled.
const instructionSource = (entry) => entry.source || entry.path;

export function buildInstructionsTree(state) {
  const entries = state.instructions?.global || [];
  const backups = state.backups || {};
  return [
    { label: "Shared instructions", files: instructionFiles(entries.filter((entry) => instructionSource(entry) === "AGENTS.md"), backups, state) },
    { label: "Overlays", files: instructionFiles(entries.filter((entry) => instructionSource(entry).startsWith("agents/")), backups, state) },
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
        meta: file.exists ? "Machines, Agents and Skill targets" : "create",
      })),
    },
  ];
}
