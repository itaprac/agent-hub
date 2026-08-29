// Checks the colour-scheme seam: the four schemes, the stored value, and the
// CSS and markup that must follow them. Node only; the repo has no JS runner.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { DEFAULT_THEME, THEME_ICONS, THEME_KEY, THEMES, normalizeTheme } from "../web/js/theme.js";

const read = (name) => readFileSync(fileURLToPath(new URL(`../${name}`, import.meta.url)), "utf8");
const css = read("web/style.css");
const html = read("web/index.html");
const app = read("web/js/app.js");

console.log("== 1. four colour schemes in menu order ==");
assert.deepEqual(THEMES, ["dark", "black", "light", "system"]);
assert.equal(DEFAULT_THEME, "dark");
assert.equal(THEME_KEY, "agent-hub:theme");
console.log("PASS");

console.log("== 2. unknown and missing stored values fall back to dark ==");
for (const theme of THEMES) assert.equal(normalizeTheme(theme), theme);
for (const value of [null, undefined, "", "amoled", "Dark", 0]) {
  assert.equal(normalizeTheme(value), "dark");
}
console.log("PASS");

console.log("== 3. every scheme has its own icon ==");
const icons = THEMES.map((theme) => THEME_ICONS[theme]);
for (const icon of icons) assert.equal(typeof icon, "string");
assert.equal(new Set(icons).size, THEMES.length);
console.log("PASS");

console.log("== 4. Black paints a pure black canvas over lighter cards ==");
const black = css.match(/html\[data-theme="black"\]\s*\{[^}]*\}/);
assert.ok(black, "style.css has no html[data-theme=\"black\"] block");
const blackBlock = black[0];
assert.match(blackBlock, /color-scheme:\s*dark/);
for (const token of ["--bg", "--rail-bg"]) {
  assert.match(blackBlock, new RegExp(`${token}:\\s*oklch\\(0 0 0\\)`), `${token} is not pure black`);
}
for (const token of ["--panel", "--card", "--card-2", "--sunken", "--log-bg"]) {
  const declaration = blackBlock.match(new RegExp(`${token}:\\s*([^;]+);`));
  assert.ok(declaration, `Black does not set ${token}`);
  assert.doesNotMatch(declaration[1], /oklch\(0 0 0\)/, `${token} must lift off the canvas`);
}
console.log("PASS");

console.log("== 5. System never resolves to Black ==");
// Reads each at-rule to its matching brace, so a growing stylesheet cannot
// slip a Black rule past this check.
function block(source, start) {
  let depth = 0;
  for (let at = source.indexOf("{", start); at < source.length; at += 1) {
    if (source[at] === "{") depth += 1;
    else if (source[at] === "}" && (depth -= 1) === 0) return source.slice(start, at + 1);
  }
  throw new Error("unbalanced braces in style.css");
}
const schemeQueries = [...css.matchAll(/@media\s*\(prefers-color-scheme:[^)]*\)/g)];
assert.ok(schemeQueries.length > 0, "style.css has no prefers-color-scheme rule");
for (const match of schemeQueries) {
  assert.doesNotMatch(block(css, match.index), /data-theme="black"/);
}
console.log("PASS");

console.log("== 6. the top bar opens a menu instead of cycling ==");
assert.doesNotMatch(app, /cycleTheme/);
assert.doesNotMatch(html, /click for/i);
assert.match(html, /id="theme-menu"/);
assert.match(html, /aria-haspopup="menu"/);
assert.match(html, /aria-expanded="false"/);
console.log("PASS");

console.log("WEB THEME TEST PASSED");
