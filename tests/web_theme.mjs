// Exercise the mounted colour-scheme control with a small DOM stand-in.
import assert from "node:assert/strict";
import { DEFAULT_THEME, THEME_KEY, THEMES, mountTheme, normalizeTheme } from "../web/js/theme.js";

for (const theme of ["dark", "black", "light", "system"]) assert.equal(normalizeTheme(theme), theme);
for (const value of [null, undefined, "", "amoled", "Dark", 0]) {
  assert.equal(normalizeTheme(value), "dark");
}

// Only the DOM operations used by the picker and shared element helper.
class TestElement extends EventTarget {
  children = [];
  dataset = {};
  attributes = new Map();
  className = "";
  append(child) { child.parent = this; this.children.push(child); }
  get firstChild() { return this.children[0]; }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); child.parent = null; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  matches(selector) { return selector.startsWith("#") ? this.id === selector.slice(1) : this.className.split(" ").includes(selector.slice(1)); }
  querySelectorAll(selector) { return this.children.flatMap((child) => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; }
  closest(selector) { return this.matches(selector) ? this : this.parent?.closest(selector); }
  contains(node) { return node === this || this.children.some((child) => child.contains(node)); }
  focus() { document.activeElement = this; }
  click() { this.dispatchEvent(new Event("click")); }
}

function setup(stored, blocked = false) {
  globalThis.Node = globalThis.Element = TestElement;
  globalThis.document = new TestElement();
  document.createElement = () => new TestElement();
  document.documentElement = new TestElement();
  const picker = new TestElement();
  picker.className = "theme-picker";
  document.append(picker);
  const button = new TestElement();
  button.id = "btn-theme";
  picker.append(button);
  for (const id of ["theme-name", "theme-icon"]) {
    const node = new TestElement();
    node.id = id;
    button.append(node);
  }
  const menu = new TestElement();
  menu.id = "theme-menu";
  menu.hidden = true;
  picker.append(menu);
  const storage = new Map([[THEME_KEY, stored]]);
  globalThis.localStorage = {
    getItem(key) { if (blocked) throw new Error("storage denied"); return storage.get(key); },
    setItem(key, value) { if (blocked) throw new Error("storage denied"); storage.set(key, value); },
  };
  mountTheme();
  const key = (value) => {
    const event = new Event("keydown", { cancelable: true });
    event.key = value;
    picker.dispatchEvent(event);
    return event.defaultPrevented;
  };
  return { button, menu, storage, key };
}

const { button, menu, storage, key } = setup("black");
assert.deepEqual(menu.children.map((item) => item.dataset.theme), ["dark", "black", "light", "system"]);
assert.equal(document.documentElement.dataset.theme, "black");
assert.equal(button.querySelector("#theme-name").textContent, "black");
button.click();
assert.equal(menu.hidden, false);
assert.equal(button.getAttribute("aria-expanded"), "true");
assert.equal(document.activeElement, menu.children[1]);
assert.equal(storage.get(THEME_KEY), "black");
button.click();
assert.equal(menu.hidden, true);
assert.equal(document.activeElement, button);

assert.equal(key("ArrowDown"), true);
assert.equal(document.activeElement, menu.children[0]);
key("ArrowUp");
assert.equal(document.activeElement, menu.children[3]);
key("ArrowDown");
assert.equal(document.activeElement, menu.children[0]);
key("End");
assert.equal(document.activeElement, menu.children[3]);
key("Home");
assert.equal(document.activeElement, menu.children[0]);
assert.equal(key("Escape"), true);
assert.equal(menu.hidden, true);
assert.equal(document.activeElement, button);
key("ArrowUp");
assert.equal(document.activeElement, menu.children[3]);

for (const theme of THEMES) {
  if (menu.hidden) button.click();
  menu.children.find((item) => item.dataset.theme === theme).click();
  assert.equal(document.documentElement.dataset.theme, theme);
  assert.equal(storage.get(THEME_KEY), theme);
  assert.equal(button.querySelector("#theme-name").textContent, theme);
  assert.equal(menu.hidden, true);
  assert.equal(button.getAttribute("aria-expanded"), "false");
  assert.equal(document.activeElement, button);
  assert.deepEqual(menu.children.filter((item) => item.getAttribute("aria-checked") === "true").map((item) => item.dataset.theme), [theme]);
}

// A click on inert chrome returns focus; a focus move elsewhere keeps it there.
button.click();
document.dispatchEvent(new Event("pointerdown"));
assert.equal(menu.hidden, true);
assert.equal(document.activeElement, button);
button.click();
const outside = new TestElement();
outside.focus();
document.dispatchEvent(new Event("focusin"));
assert.equal(menu.hidden, true);
assert.equal(document.activeElement, outside);

setup(storage.get(THEME_KEY));
assert.equal(document.documentElement.dataset.theme, "system");
setup("obsolete");
assert.equal(document.documentElement.dataset.theme, DEFAULT_THEME);
const privateMode = setup("black", true);
assert.equal(document.documentElement.dataset.theme, DEFAULT_THEME);
privateMode.button.click();
privateMode.menu.children[1].click();
assert.equal(document.documentElement.dataset.theme, "black");
assert.equal(privateMode.menu.hidden, true);
console.log("WEB THEME TEST PASSED");
