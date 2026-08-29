// Colour scheme: the four schemes, the stored choice, and the top-bar menu.

import { $, $$, clear, el } from "./dom.js";

export const THEME_KEY = "agent-hub:theme";

// Menu order. Dark is the designed default. System follows the OS appearance
// and maps OS dark to Dark, never to Black.
export const THEMES = ["dark", "black", "light", "system"];
export const DEFAULT_THEME = "dark";

export const THEME_ICONS = {
  dark: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M13.2 9.6A5.6 5.6 0 0 1 6.4 2.8a5.6 5.6 0 1 0 6.8 6.8Z" fill="currentColor"/></svg>',
  black: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="8" cy="8" r="5.4" fill="currentColor"/></svg>',
  light: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" aria-hidden="true"><circle cx="8" cy="8" r="2.6"/><path d="M8 1.6v1.6M8 12.8v1.6M1.6 8h1.6M12.8 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M12.6 3.4l-1.1 1.1M4.5 11.5l-1.1 1.1"/></svg>',
  system: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="8" cy="8" r="5.2" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M8 2.8v10.4A5.2 5.2 0 0 0 8 2.8Z" fill="currentColor"/></svg>',
};

export function normalizeTheme(value) {
  return THEMES.includes(value) ? value : DEFAULT_THEME;
}

function readTheme() {
  try {
    return normalizeTheme(localStorage.getItem(THEME_KEY));
  } catch (error) {
    return DEFAULT_THEME; /* private mode: ignore */
  }
}

let button = null;
let menu = null;

const items = () => $$(".theme-option", menu);
const insidePicker = (node) => node instanceof Element && node.closest(".theme-picker");

function applyTheme(value) {
  const theme = normalizeTheme(value);
  document.documentElement.dataset.theme = theme;
  $("#theme-name", button).textContent = theme;
  $("#theme-icon", button).innerHTML = THEME_ICONS[theme];
  button.title = `Colour scheme: ${theme}`;
  for (const item of items()) {
    item.setAttribute("aria-checked", String(item.dataset.theme === theme));
  }
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch (error) {
    /* private mode: ignore */
  }
}

function isOpen() {
  return !menu.hidden;
}

function openMenu(focus = "current") {
  menu.hidden = false;
  button.setAttribute("aria-expanded", "true");
  const options = items();
  const current = options.find((item) => item.getAttribute("aria-checked") === "true");
  const target = { current, first: options[0], last: options[options.length - 1] }[focus];
  (target || options[0]).focus();
}

// `focus` returns focus to the trigger. Pass it whenever the menu still holds
// focus, so hiding it never drops focus onto the document body.
function closeMenu({ focus = false } = {}) {
  if (!isOpen()) return;
  menu.hidden = true;
  button.setAttribute("aria-expanded", "false");
  if (focus) button.focus();
}

function onKeydown(event) {
  if (!isOpen()) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      openMenu(event.key === "ArrowUp" ? "last" : "first");
    }
    return;
  }
  if (event.key === "Escape") {
    event.preventDefault();
    closeMenu({ focus: true });
    return;
  }
  // Tab is left to the browser: the options are not tabbable, so focus leaves
  // the picker and the focusin listener below closes the menu behind it.
  const options = items();
  const index = options.indexOf(document.activeElement);
  if (index < 0) return;
  const move = { ArrowDown: index + 1, ArrowUp: index - 1, Home: 0, End: options.length - 1 }[event.key];
  if (move === undefined) return;
  event.preventDefault();
  options[(move + options.length) % options.length].focus();
}

// Mounts the top-bar control and applies the stored scheme.
export function mountTheme() {
  button = $("#btn-theme");
  menu = $("#theme-menu");
  clear(menu);
  for (const theme of THEMES) {
    menu.append(
      el(
        "button",
        {
          type: "button",
          class: "theme-option",
          role: "menuitemradio",
          "aria-checked": "false",
          tabIndex: -1,
          dataset: { theme },
          onClick: () => {
            applyTheme(theme);
            closeMenu({ focus: true });
          },
        },
        [
          el("span", { class: "theme-option-icon", "aria-hidden": "true", html: THEME_ICONS[theme] }),
          el("span", { class: "theme-option-name", text: theme }),
        ]
      )
    );
  }

  button.addEventListener("click", () => (isOpen() ? closeMenu({ focus: true }) : openMenu()));
  button.closest(".theme-picker").addEventListener("keydown", onKeydown);
  document.addEventListener("pointerdown", (event) => {
    // A click on inert chrome cannot take focus, so hand it back to the trigger.
    if (isOpen() && !insidePicker(event.target)) closeMenu({ focus: menu.contains(document.activeElement) });
  });
  document.addEventListener("focusin", (event) => {
    if (isOpen() && !insidePicker(event.target)) closeMenu();
  });

  applyTheme(readTheme());
}
