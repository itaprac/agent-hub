// Layout switch between the Workbench (/) and Editorial (/editorial/) Consoles.
// Both are complete front ends over the same API; the choice lives in localStorage
// and web/js/layout-redirect.js applies it before the Workbench paints.

export const LAYOUT_KEY = "agent-hub-layout";

export const LAYOUTS = {
  workbench: { label: "Workbench", path: "/" },
  editorial: { label: "Editorial", path: "/editorial/" },
};

export function otherLayout(current) {
  return current === "editorial" ? "workbench" : "editorial";
}

export function switchLayout(target) {
  try {
    localStorage.setItem(LAYOUT_KEY, target);
  } catch (error) {
    /* ignore */
  }
  location.assign(LAYOUTS[target].path + location.hash);
}

// Wires every [data-layout-switch] button and the M shortcut.
export function mountLayoutSwitch(current) {
  try {
    localStorage.setItem(LAYOUT_KEY, current);
  } catch (error) {
    /* ignore */
  }
  const target = otherLayout(current);
  const label = `Switch to ${LAYOUTS[target].label} layout (M)`;
  for (const button of document.querySelectorAll("[data-layout-switch]")) {
    button.title = label;
    button.setAttribute("aria-label", label);
    const text = button.querySelector("[data-layout-label]");
    if (text) text.textContent = LAYOUTS[target].label;
    button.addEventListener("click", () => switchLayout(target));
  }
  document.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
    if (event.key.toLowerCase() !== "m") return;
    const node = event.target;
    const typing = node instanceof HTMLElement
      && (node.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(node.tagName));
    if (typing || document.querySelector("dialog[open]")) return;
    event.preventDefault();
    switchLayout(target);
  });
}
