// Settings page: optional usage sources. Opened from the rail footer.

import { api } from "./api.js";
import { $, clear, el, toast } from "./dom.js";
import { MARK, PROVIDER_LABEL } from "./brands.js";
import { store, update } from "./store.js";

let painted = null;

export function createSettingsController({ request, publish = () => {}, render = () => {}, reportError = () => {} }) {
  let state = { tokenDraft: "" };
  let projected = Object.freeze({ ...state });

  const view = () => projected;
  const change = (patch) => {
    state = { ...state, ...patch };
    projected = Object.freeze({ ...state });
    render(projected);
  };

  async function save(patch) {
    try {
      const settings = await request(patch);
      if (state.tokenDraft) change({ tokenDraft: "" });
      publish(settings);
      return true;
    } catch (error) {
      render(projected);
      reportError(error);
      return false;
    }
  }

  return {
    view,
    editToken(value) {
      const tokenDraft = String(value ?? "");
      if (tokenDraft === state.tokenDraft) return false;
      change({ tokenDraft });
      return true;
    },
    save,
    saveToken: () => save({ cursorToken: state.tokenDraft }),
    clearToken: () => save({ cursorToken: "" }),
  };
}

const controller = createSettingsController({
  request: (patch) => api.saveUsageSettings(patch),
  publish: (usageSettings) => update({ usageSettings, usage: null }),
  render() {
    painted = null;
    paint(store);
  },
  reportError: (error) => toast(error.message, "err", 6000),
});

function sourceRow(name, checked, title, detail, extra) {
  return el("div", { class: "settings-source" }, [
    el("span", { class: `usage-mark usage-mark-${name}`, html: MARK[name] }),
    el("div", { class: "settings-source-copy" }, [
      el("div", { class: "settings-source-name", text: title }),
      el("p", { class: "settings-source-detail", text: detail }),
      extra,
    ]),
    el("label", { class: "switch" }, [
      el("input", { type: "checkbox", checked: Boolean(checked), dataset: { usageToggle: name } }),
      el("span", { class: "sw" }),
      el("span", { class: "sr-only", text: `Track ${title}` }),
    ]),
  ]);
}

function tokenForm(settings, tokenDraft) {
  return el("form", { class: "usage-token", dataset: { usageTokenForm: "1" } }, [
    el("label", { class: "usage-token-label", text: "Session token" }),
    el("input", {
      class: "usage-token-input",
      type: "password",
      name: "cursorToken",
      autocomplete: "off",
      spellcheck: "false",
      placeholder: settings.cursorTokenSet ? "Token saved on this machine" : "WorkosCursorSessionToken",
      value: tokenDraft,
    }),
    el("button", { class: "btn btn-sm btn-primary", type: "submit", text: "Save" }),
    settings.cursorTokenSet
      ? el("button", { class: "btn btn-sm", type: "button", text: "Clear", dataset: { usageClearToken: "1" } })
      : null,
  ]);
}

function paint(snapshot) {
  const root = $("#settings-root");
  if (!root) return;
  const view = controller.view();
  const settings = snapshot.usageSettings || snapshot.usage?.settings || null;
  const key = [snapshot.tab, settings, view];
  if (painted && painted.every((item, index) => item === key[index])) return;
  if (snapshot.tab !== "settings") {
    painted = key;
    return;
  }

  clear(root);
  if (!settings) {
    root.append(el("p", { class: "settings-note", text: "Loading usage settings…" }));
    painted = key;
    return;
  }
  root.append(
    el("div", { class: "settings-head" }, [
      el("h1", { class: "title", text: "Settings" }),
      el("p", { class: "settings-lead", text: "Machine-local options. They are not stored in Git." }),
    ]),
    el("section", { class: "sec" }, [
      el("div", { class: "sec-head" }, [el("h2", { class: "sec-title", text: "Usage sources" })]),
      el("p", {
        class: "settings-note",
        text: "Choose which local transcripts and APIs this machine includes in Usage.",
      }),
      el("div", { class: "settings-sources" }, [
        sourceRow(
          "claude",
          settings.claude,
          PROVIDER_LABEL.claude,
          "Reads ~/.claude/projects on this machine.",
        ),
        sourceRow(
          "codex",
          settings.codex,
          PROVIDER_LABEL.codex,
          "Reads ~/.codex/sessions on this machine.",
        ),
        sourceRow(
          "grok",
          settings.grok,
          PROVIDER_LABEL.grok,
          "Reads completed turns from ~/.grok/sessions on this machine.",
        ),
        sourceRow(
          "cursor",
          settings.cursor,
          PROVIDER_LABEL.cursor,
          "Reads billed events from the Cursor dashboard API.",
          settings.cursor
            ? el("div", { class: "settings-source-extra" }, [
                tokenForm(settings, view.tokenDraft),
                el(
                  "p",
                  {
                    class: "usage-settings-hint",
                    text: "Copy WorkosCursorSessionToken from the cookies on cursor.com/dashboard. Renew it when Cursor usage fails with an expired token.",
                  },
                ),
              ])
            : null,
        ),
      ]),
    ]),
  );
  painted = key;
}

export function renderSettings(snapshot) {
  paint(snapshot);
}

export function mountSettings() {
  const root = $("#settings-root");
  if (!root) return;
  api.usageSettings().then((usageSettings) => update({ usageSettings })).catch(() => {});

  root.addEventListener("change", (event) => {
    const toggle = event.target.closest("[data-usage-toggle]");
    if (!toggle) return;
    controller.save({ [toggle.dataset.usageToggle]: toggle.checked });
  });
  root.addEventListener("input", (event) => {
    if (event.target.closest("[name=cursorToken]")) {
      controller.editToken(event.target.value);
    }
  });
  root.addEventListener("click", (event) => {
    if (!event.target.closest("[data-usage-clear-token]")) return;
    controller.clearToken();
  });
  root.addEventListener("submit", (event) => {
    const form = event.target.closest("[data-usage-token-form]");
    if (!form) return;
    event.preventDefault();
    controller.saveToken();
  });
}
