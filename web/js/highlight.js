// Line-based syntax highlighting for the editor overlay.
// Every tokenizer takes one line and the state left by the previous line, and
// returns [html, nextState]. Results are memoised per (state, line) so an edit
// re-tokenizes only what changed and the lines whose state it moved.

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };
const esc = (text) => text.replace(/[&<>"]/g, (char) => ESCAPES[char]);
const tok = (kind, text) => (text ? `<span class="tk-${kind}">${esc(text)}</span>` : "");
const wrap = (kind, html) => (html ? `<span class="tk-${kind}">${html}</span>` : "");

// Files above this size are shown with line numbers but without colour.
export const HIGHLIGHT_LIMIT = 400_000;

export function languageFor(path) {
  const name = String(path || "").toLowerCase();
  if (/\.(md|markdown|mdx)$/.test(name)) return "markdown";
  if (name.endsWith(".toml")) return "toml";
  if (name.endsWith(".json")) return "json";
  if (/\.ya?ml$/.test(name)) return "yaml";
  if (/\.(sh|bash|zsh)$/.test(name)) return "shell";
  if (name.endsWith(".py")) return "python";
  return "plain";
}

export const LANGUAGE_LABEL = {
  markdown: "Markdown",
  toml: "TOML",
  json: "JSON",
  yaml: "YAML",
  shell: "Shell",
  python: "Python",
  plain: "Plain text",
};

// ------------------------------------------------------------------ markdown

const INLINE = new RegExp([
  "(`+)([^`]|[^`][\\s\\S]*?[^`])\\1(?!`)", // 1,2 code span
  "(!?)\\[([^\\]\\n]*)\\]\\(([^)\\s]*)((?:\\s+\"[^\"]*\")?)\\)", // 3-6 link / image
  "\\*\\*(?=\\S)([^*]*?\\S)\\*\\*", // 7 bold
  "__(?=\\S)([^_]*?\\S)__", // 8 bold
  "(?<![\\w*])\\*(?=[^\\s*])([^*]*?[^\\s*])\\*(?![\\w*])", // 9 italic
  "(?<![\\w_])_(?=[^\\s_])([^_]*?[^\\s_])_(?![\\w_])", // 10 italic
  "~~(?=\\S)([^~]*?\\S)~~", // 11 strike
  "<(https?:\\/\\/[^>\\s]+)>", // 12 autolink
  "(https?:\\/\\/[^\\s<>()]*[^\\s<>().,;:!?'\"])", // 13 bare url
  "(<!--.*?-->)", // 14 comment
  "(<\\/?[A-Za-z][\\w-]*(?:\\s[^<>]*)?\\/?>)", // 15 html tag
].join("|"), "g");

function inline(text) {
  if (!text) return "";
  let out = "";
  let last = 0;
  // matchAll works on a copy of the pattern, so nested calls do not disturb it.
  for (const match of text.matchAll(INLINE)) {
    out += esc(text.slice(last, match.index));
    last = match.index + match[0].length;
    if (match[1] !== undefined) out += tok("code", match[0]);
    else if (match[4] !== undefined) {
      out += `<span class="tk-link">${esc(`${match[3]}[`)}<span class="tk-link-text">${inline(match[4])}</span>${esc("](")}<span class="tk-url">${esc(match[5])}</span>${esc(`${match[6]})`)}</span>`;
    } else if (match[7] !== undefined || match[8] !== undefined) {
      const mark = match[0].slice(0, 2);
      out += `<span class="tk-b"><span class="tk-mark">${mark}</span>${inline(match[7] ?? match[8])}<span class="tk-mark">${mark}</span></span>`;
    } else if (match[9] !== undefined || match[10] !== undefined) {
      const mark = match[0][0];
      out += `<span class="tk-i"><span class="tk-mark">${mark}</span>${inline(match[9] ?? match[10])}<span class="tk-mark">${mark}</span></span>`;
    } else if (match[11] !== undefined) out += tok("s", match[0]);
    else if (match[12] !== undefined || match[13] !== undefined) out += tok("url", match[0]);
    else if (match[14] !== undefined) out += tok("cmt", match[0]);
    else out += tok("tag", match[0]);
  }
  return out + esc(text.slice(last));
}

function markdown(line, state) {
  if (state === "start") {
    if (/^---\s*$/.test(line)) return [tok("fm", line), "fm"];
    state = "";
  }
  if (state.startsWith("fm")) {
    if (/^(---|\.\.\.)\s*$/.test(line)) return [tok("fm", line), ""];
    const [html, next] = yaml(line, state.slice(3));
    return [html, `fm:${next}`];
  }
  if (state.startsWith("code:")) {
    const fence = state.slice(5);
    const trimmed = line.trim();
    if (trimmed.startsWith(fence) && /^[`~]*$/.test(trimmed) && trimmed[0] === fence[0]) return [tok("fence", line), ""];
    return [tok("pre", line), state];
  }
  if (state === "cmt") {
    const end = line.indexOf("-->");
    if (end < 0) return [tok("cmt", line), "cmt"];
    return [tok("cmt", line.slice(0, end + 3)) + inline(line.slice(end + 3)), ""];
  }

  let match = /^(\s*)(`{3,}|~{3,})(.*)$/.exec(line);
  if (match) return [esc(match[1]) + tok("fence", match[2]) + tok("lang", match[3]), `code:${match[2]}`];
  match = /^(#{1,6})(\s+|$)(.*)$/.exec(line);
  if (match) return [`<span class="tk-h tk-h${match[1].length}"><span class="tk-mark">${match[1]}</span>${esc(match[2])}${inline(match[3])}</span>`, ""];
  if (/^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(line)) return [tok("mark", line), ""];
  if (/^\s*(=+|-+)\s*$/.test(line) && line.trim()) return [tok("mark", line), ""];
  match = /^(\s*>[>\s]*)(.*)$/.exec(line);
  if (match) return [tok("mark", match[1]) + wrap("quote", inline(match[2])), ""];
  match = /^(\s*)([-*+]|\d{1,9}[.)])(\s+)(\[[ xX]\]\s)?(.*)$/.exec(line);
  if (match) return [esc(match[1]) + tok("list", match[2]) + esc(match[3]) + tok("list", match[4] || "") + inline(match[5]), ""];
  match = /^(\s*)(<!--)(.*)$/.exec(line);
  if (match && !match[3].includes("-->")) return [esc(match[1]) + tok("cmt", match[2] + match[3]), "cmt"];
  if (/^\s*\|/.test(line)) return [line.split("|").map((cell) => inline(cell)).join(tok("mark", "|")), ""];
  return [inline(line), ""];
}

// ------------------------------------------------------------------ yaml

function scalar(text) {
  const trimmed = text.trim();
  if (!trimmed) return esc(text);
  const lead = text.slice(0, text.indexOf(trimmed));
  const tail = text.slice(lead.length + trimmed.length);
  let body;
  if (/^(["']).*\1$/.test(trimmed)) body = tok("str", trimmed);
  else if (/^(true|false|yes|no|on|off|null|~)$/i.test(trimmed)) body = tok("kw", trimmed);
  else if (/^[-+]?(\d[\d_]*)(\.\d+)?([eE][-+]?\d+)?$/.test(trimmed)) body = tok("num", trimmed);
  else if (/^[|>][-+]?\d*$/.test(trimmed)) body = tok("mark", trimmed);
  else if (/^\[.*\]$|^\{.*\}$/.test(trimmed)) body = trimmed.split(/([[\]{},])/).map((part) => (/^[[\]{},]$/.test(part) ? tok("mark", part) : scalar(part))).join("");
  else body = tok("val", trimmed);
  return esc(lead) + body + esc(tail);
}

function splitComment(text) {
  const match = /(^|\s)#/.exec(text);
  if (!match) return [text, ""];
  let quote = null;
  for (let index = 0; index < match.index + match[1].length; index += 1) {
    const char = text[index];
    if (quote) { if (char === quote) quote = null; } else if (char === '"' || char === "'") quote = char;
  }
  if (quote) return [text, ""];
  const at = match.index + match[1].length;
  return [text.slice(0, at), text.slice(at)];
}

function yaml(line, state) {
  // Inside a block scalar (key: | or >), deeper-indented lines are plain text.
  if (state.startsWith("block:")) {
    const indent = Number(state.slice(6));
    const own = line.length - line.trimStart().length;
    if (!line.trim() || own > indent) return [tok("val", line), state];
    state = "";
  }
  if (/^\s*#/.test(line)) return [tok("cmt", line), state];
  if (/^(---|\.\.\.)\s*$/.test(line)) return [tok("mark", line), state];
  const [code, comment] = splitComment(line);
  let match = /^(\s*)(-\s+)?((?:"[^"]*"|'[^']*'|[^\s:#][^:#]*?))(\s*:)(\s|$)(.*)$/.exec(code);
  if (match) {
    const next = /^[|>][-+]?\d*\s*$/.test(match[6]) ? `block:${match[1].length + (match[2] || "").length}` : state;
    return [esc(match[1]) + tok("mark", match[2] || "") + tok("key", match[3]) + tok("mark", match[4]) + esc(match[5]) + scalar(match[6]) + tok("cmt", comment), next];
  }
  match = /^(\s*)(-\s+|-$)(.*)$/.exec(code);
  if (match) return [esc(match[1]) + tok("mark", match[2]) + scalar(match[3]) + tok("cmt", comment), state];
  return [scalar(code) + tok("cmt", comment), state];
}

// ------------------------------------------------------------------ toml

const TOML_VALUE = /("""|''')|("(?:[^"\\]|\\.)*"?|'[^']*'?)|(#.*$)|(\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+(?:Z|[-+]\d{2}:\d{2})?)?)|\b(true|false|inf|nan)\b|([-+]?(?:0x[\da-fA-F_]+|0o[0-7_]+|0b[01_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][-+]?\d+)?))|([[\]{},=])/g;

function tomlValue(text) {
  let out = "";
  let last = 0;
  TOML_VALUE.lastIndex = 0;
  let match;
  while ((match = TOML_VALUE.exec(text))) {
    out += esc(text.slice(last, match.index));
    if (match[1]) {
      // A multi-line string opens here; it may close on the same line.
      const close = text.indexOf(match[1], match.index + 3);
      if (close < 0) return [out + tok("str", text.slice(match.index)), match[1]];
      out += tok("str", text.slice(match.index, close + 3));
      last = close + 3;
      TOML_VALUE.lastIndex = last;
      continue;
    }
    last = match.index + match[0].length;
    if (match[2]) out += tok("str", match[0]);
    else if (match[3]) out += tok("cmt", match[0]);
    else if (match[4]) out += tok("num", match[0]);
    else if (match[5]) out += tok("kw", match[0]);
    else if (match[6]) out += tok("num", match[0]);
    else out += tok("mark", match[0]);
  }
  return [out + esc(text.slice(last)), ""];
}

function toml(line, state) {
  if (state === '"""' || state === "'''") {
    const close = line.indexOf(state);
    if (close < 0) return [tok("str", line), state];
    const [rest, next] = tomlValue(line.slice(close + 3));
    return [tok("str", line.slice(0, close + 3)) + rest, next];
  }
  if (/^\s*#/.test(line)) return [tok("cmt", line), ""];
  let match = /^(\s*)(\[\[?)([^\]]*)(\]\]?)(.*)$/.exec(line);
  if (match) return [esc(match[1]) + tok("mark", match[2]) + tok("table", match[3]) + tok("mark", match[4]) + tomlValue(match[5])[0], ""];
  match = /^(\s*)((?:"[^"]*"|'[^']*'|[A-Za-z0-9_.-]+)(?:\s*\.\s*(?:"[^"]*"|'[^']*'|[A-Za-z0-9_-]+))*)(\s*=\s*)(.*)$/.exec(line);
  if (match) {
    const [value, next] = tomlValue(match[4]);
    return [esc(match[1]) + tok("key", match[2]) + tok("mark", match[3]) + value, next];
  }
  return tomlValue(line);
}

// ------------------------------------------------------------------ json

const JSON_TOKEN = /("(?:[^"\\]|\\.)*"?)(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)|([[\]{},:])/g;

function json(line, state) {
  let out = "";
  let last = 0;
  JSON_TOKEN.lastIndex = 0;
  let match;
  while ((match = JSON_TOKEN.exec(line))) {
    out += esc(line.slice(last, match.index));
    last = match.index + match[0].length;
    if (match[1] !== undefined) out += match[2] ? tok("key", match[1]) + tok("mark", match[2]) : tok("str", match[1]);
    else if (match[3]) out += tok("kw", match[0]);
    else if (match[4]) out += tok("num", match[0]);
    else out += tok("mark", match[0]);
  }
  return [out + esc(line.slice(last)), state];
}

// ------------------------------------------------------------------ shell / python

const SCRIPT_TOKEN = /("(?:[^"\\]|\\.)*"?|'[^']*'?)|((?:^|\s)#.*$)|\b(\d+(?:\.\d+)?)\b|(\$\{?[A-Za-z_][\w]*\}?)/g;

function script(keywords) {
  const words = new RegExp(`\\b(${keywords.join("|")})\\b`, "g");
  return (line, state) => {
    let out = "";
    let last = 0;
    SCRIPT_TOKEN.lastIndex = 0;
    let match;
    const plain = (text) => esc(text).replace(words, '<span class="tk-kw">$1</span>');
    while ((match = SCRIPT_TOKEN.exec(line))) {
      out += plain(line.slice(last, match.index));
      last = match.index + match[0].length;
      if (match[1]) out += tok("str", match[0]);
      else if (match[2]) {
        const lead = match[0].length - match[0].trimStart().length;
        out += esc(match[0].slice(0, lead)) + tok("cmt", match[0].slice(lead));
      } else if (match[3]) out += tok("num", match[0]);
      else out += tok("key", match[0]);
    }
    return [out + plain(line.slice(last)), state];
  };
}

const TOKENIZERS = {
  markdown,
  toml,
  json,
  yaml,
  shell: script(["if", "then", "else", "elif", "fi", "for", "while", "do", "done", "case", "esac", "function", "in", "return", "local", "export", "set"]),
  python: script(["def", "class", "return", "if", "elif", "else", "for", "while", "import", "from", "as", "with", "try", "except", "finally", "raise", "None", "True", "False", "and", "or", "not", "in", "is", "lambda", "yield", "async", "await"]),
};

export function createHighlighter() {
  const memo = new Map();
  return function highlight(text, language) {
    const lines = text.split("\n");
    const tokenizer = text.length > HIGHLIGHT_LIMIT ? null : TOKENIZERS[language];
    if (!tokenizer) return lines.map(esc);
    if (memo.size > 60_000) memo.clear();
    const out = new Array(lines.length);
    let state = language === "markdown" ? "start" : "";
    for (let index = 0; index < lines.length; index += 1) {
      const key = `${language}\u0000${state}\u0000${lines[index]}`;
      let hit = memo.get(key);
      if (!hit) {
        hit = tokenizer(lines[index], state);
        memo.set(key, hit);
      }
      out[index] = hit[0];
      state = hit[1];
    }
    return out;
  };
}
