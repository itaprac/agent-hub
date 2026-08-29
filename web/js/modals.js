// Native <dialog> based modals: confirmation and small forms.

import { $, clear, el } from "./dom.js";

let dialog = null;
let pending = null;
let dialogSequence = 0;

function host() {
  if (!dialog) {
    dialog = $("#modal");
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      settle(null);
    });
  }
  return dialog;
}

function shell({ title, sub, body, footer }) {
  const node = host();
  const suffix = ++dialogSequence;
  const titleId = `modal-title-${suffix}`;
  const subId = `modal-description-${suffix}`;
  const bodyId = `modal-body-${suffix}`;
  const bodyNode = el("div", { class: "modal-body", id: bodyId }, body);
  clear(node);
  node.setAttribute("aria-labelledby", titleId);
  node.setAttribute("aria-describedby", sub ? subId : bodyId);
  node.append(
    el("form", { method: "dialog", class: "modal-form", noValidate: true }, [
      el("div", { class: "modal-head" }, [
        el("div", { class: "modal-title", id: titleId, role: "heading", "aria-level": "2", text: title }),
        sub ? el("div", { class: "modal-sub", id: subId, text: sub }) : null,
      ]),
      bodyNode,
      el("div", { class: "modal-foot" }, footer),
    ])
  );
  return node;
}

function settle(value) {
  const resolve = pending;
  pending = null;
  if (dialog.open) dialog.close();
  if (resolve) resolve(value);
}

export function confirmDialog({ title, body, confirmLabel = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    if (pending) settle(null);
    pending = resolve;
    const node = shell({
      title,
      body: [el("div", { class: "modal-text", text: body })],
      footer: [
        el("button", { type: "button", class: "btn", text: "Cancel", onClick: () => settle(null) }),
        el("button", {
          type: "button",
          class: `btn ${danger ? "btn-danger" : "btn-primary"}`,
          text: confirmLabel,
          onClick: () => settle(true),
        }),
      ],
    });
    node.showModal();
    node.querySelector(".modal-foot .btn:last-child").focus();
  }).then((value) => value === true);
}

export function conflictDialog({
  title = "File changed elsewhere",
  body,
  keepLabel = "Keep editing",
  reloadLabel = "Reload latest",
  overwriteLabel = "Overwrite latest",
}) {
  return new Promise((resolve) => {
    if (pending) settle(null);
    pending = resolve;
    const node = shell({
      title,
      body: [el("div", { class: "modal-text", text: body })],
      footer: [
        el("button", {
          type: "button",
          class: "btn btn-primary",
          text: keepLabel,
          onClick: () => settle("keep"),
        }),
        el("button", {
          type: "button",
          class: "btn",
          text: reloadLabel,
          onClick: () => settle("reload"),
        }),
        el("button", {
          type: "button",
          class: "btn btn-danger",
          text: overwriteLabel,
          onClick: () => settle("overwrite"),
        }),
      ],
    });
    node.showModal();
    node.querySelector(".modal-foot .btn:first-child").focus();
  });
}

export function formDialog({ title, sub, fields, confirmLabel = "Create" }) {
  return new Promise((resolve) => {
    if (pending) settle(null);
    pending = resolve;

    const inputs = new Map();
    const errors = new Map();
    const descriptions = new Map();
    const fieldPrefix = `modal-field-${dialogSequence + 1}`;
    const body = fields.map((field, index) => {
      const inputId = `${fieldPrefix}-${index}`;
      const hintId = `${inputId}-hint`;
      const errorId = `${inputId}-error`;
      let input;
      if (field.type === "select") {
        input = el(
          "select",
          {
            id: inputId,
            name: field.name,
            required: Boolean(field.required),
            "aria-required": field.required ? "true" : null,
          },
          (field.options || []).map((option) =>
            el("option", { value: option.value, text: option.label, selected: option.value === field.value })
          )
        );
      } else {
        input = el("input", {
          type: "text",
          id: inputId,
          name: field.name,
          value: field.value || "",
          placeholder: field.placeholder || "",
          required: Boolean(field.required),
          "aria-required": field.required ? "true" : null,
          spellcheck: false,
          autocapitalize: "off",
          autocorrect: "off",
        });
      }
      const error = el("div", {
        class: "hint field-error",
        id: errorId,
        role: "alert",
        text: `${field.label} is required.`,
        hidden: true,
      });
      const baseDescription = field.hint ? hintId : "";
      if (baseDescription) input.setAttribute("aria-describedby", baseDescription);
      const clearError = () => {
        input.removeAttribute("aria-invalid");
        if (baseDescription) input.setAttribute("aria-describedby", baseDescription);
        else input.removeAttribute("aria-describedby");
        error.hidden = true;
      };
      input.addEventListener("input", clearError);
      input.addEventListener("change", clearError);
      inputs.set(field.name, input);
      errors.set(field.name, error);
      descriptions.set(field.name, { base: baseDescription, error: errorId });
      return el("div", { class: "field" }, [
        el("label", { text: field.label, for: inputId }),
        input,
        field.hint ? el("div", { class: "hint", id: hintId, text: field.hint }) : null,
        error,
      ]);
    });

    const submit = () => {
      const values = {};
      for (const [name, input] of inputs) {
        values[name] = input.value.trim();
        input.removeAttribute("aria-invalid");
        const description = descriptions.get(name);
        if (description.base) input.setAttribute("aria-describedby", description.base);
        else input.removeAttribute("aria-describedby");
        errors.get(name).hidden = true;
      }
      const required = fields.find((field) => field.required && !values[field.name]);
      if (required) {
        const input = inputs.get(required.name);
        const description = descriptions.get(required.name);
        input.setAttribute("aria-invalid", "true");
        input.setAttribute("aria-describedby", [description.base, description.error].filter(Boolean).join(" "));
        errors.get(required.name).hidden = false;
        input.focus();
        return;
      }
      settle(values);
    };

    const node = shell({
      title,
      sub,
      body,
      footer: [
        el("button", { type: "button", class: "btn", text: "Cancel", onClick: () => settle(null) }),
        el("button", { type: "submit", class: "btn btn-primary", text: confirmLabel }),
      ],
    });
    const form = node.querySelector(".modal-form");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      submit();
    });
    node.showModal();
    const first = inputs.values().next().value;
    if (first) first.focus();
  });
}

export function projectField(projects, { name = "project", label = "Project", hint } = {}) {
  return {
    name,
    label,
    type: "select",
    value: "",
    hint,
    options: [{ value: "", label: "— global —" }].concat(
      projects.map((project) => ({
        value: project.name,
        label: project.available ? project.name : `${project.name} (not on this machine)`,
      }))
    ),
  };
}
