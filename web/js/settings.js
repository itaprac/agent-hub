// Settings page: optional usage sources. Opened from the rail footer.

import { api } from "./api.js";
import { $, clear, el, toast } from "./dom.js";
import { MARK, PROVIDER_LABEL } from "./brands.js";
import { store, update } from "./store.js";
import {
  createLocalController,
  initialSettingsViewState,
  reduceSettingsView,
} from "./view-state.js";

let painted = null;
const controller = createLocalController(initialSettingsViewState, reduceSettingsView);

export async function persistUsageSettings(patch) {
  const usageSettings = await api.saveUsageSettings(patch);
  controller.dispatch({ type: "save-finished" });
  update({ usageSettings, usage: null });
  return usageSettings;
}

function reportPersistError(error) {
  controller.dispatch({ type: "save-failed" });
  painted = null;
  paint(store);
  toast(error.message, "err", 6000);
}

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
  const view = controller.state;
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
    persistUsageSettings({ [toggle.dataset.usageToggle]: toggle.checked }).catch(reportPersistError);
  });
  root.addEventListener("input", (event) => {
    if (event.target.closest("[name=cursorToken]")) {
      controller.dispatch({ type: "edit-token", value: event.target.value });
    }
  });
  root.addEventListener("click", (event) => {
    if (!event.target.closest("[data-usage-clear-token]")) return;
    persistUsageSettings({ cursorToken: "" }).catch(reportPersistError);
  });
  root.addEventListener("submit", (event) => {
    const form = event.target.closest("[data-usage-token-form]");
    if (!form) return;
    event.preventDefault();
    persistUsageSettings({ cursorToken: controller.state.tokenDraft }).catch(reportPersistError);
  });
}
