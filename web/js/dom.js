// Tiny DOM helpers shared by every view.

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

// Assign via CSSOM properties. A string assigned to node.style goes through
// cssText, which CSP `style-src 'self'` blocks (and WebKit may throw).
function applyStyle(node, value) {
  if (!value) return;
  if (typeof value === "string") {
    for (const part of value.split(";")) {
      const index = part.indexOf(":");
      if (index < 0) continue;
      const prop = part.slice(0, index).trim();
      if (prop) node.style.setProperty(prop, part.slice(index + 1).trim());
    }
    return;
  }
  for (const [prop, amount] of Object.entries(value)) {
    if (amount === null || amount === undefined || amount === false) continue;
    if (prop.startsWith("--")) node.style.setProperty(prop, String(amount));
    else node.style[prop] = String(amount);
  }
}

export function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key === "style") applyStyle(node, value);
    else if (key.startsWith("on")) node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key in node && key !== "list") node[key] = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

export function splitPath(path) {
  const index = path.lastIndexOf("/");
  return index < 0 ? ["", path] : [path.slice(0, index + 1), path.slice(index + 1)];
}

export function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10240 ? 1 : 0)} kB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

export function formatTime(date = new Date()) {
  return date.toTimeString().slice(0, 8);
}

let toastHost = null;

export function toast(message, kind = "info", timeout = 4200) {
  toastHost = toastHost || $("#toasts");
  const isError = kind === "err";
  const node = el(
    "div",
    {
      class: `toast ${kind}`,
      role: isError ? "alert" : "status",
      "aria-live": isError ? "assertive" : "polite",
      "aria-atomic": "true",
    },
    [el("div", { class: "toast-body", text: message })]
  );
  toastHost.append(node);
  const remove = () => {
    node.style.transition = "opacity .15s ease";
    node.style.opacity = "0";
    setTimeout(() => node.remove(), 160);
  };
  node.addEventListener("click", remove);
  setTimeout(remove, timeout);
  return node;
}
