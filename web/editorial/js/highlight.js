// Line-based syntax highlighting for the editor overlay.
// Each language is a function (line, state) -> { html, state, cls }. State is a
// short string, so the editor can re-highlight only the lines an edit changed
// and stop as soon as the state after an edit matches the old one.

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };
export const escapeHtml = (text) => String(text).replace(/[&<>"]/g, (ch) => ESCAPES[ch]);
const span = (cls, text) => (text ? `<span class="${cls}">${escapeHtml(text)}</span>` : "");

// Tokenise `text` with a global regex whose match handler returns HTML.
function scan(text, regex, onMatch) {
  let html = "";
  let last = 0;
  regex.lastIndex = 0;
  for (let match = regex.exec(text); match; match = regex.exec(text)) {
    if (match[0] === "") {
      regex.lastIndex += 1;
      continue;
    }
    // A nested scan (link text) reuses the regex, so restore its position after.
    const end = match.index + match[0].length;
    html += escapeHtml(text.slice(last, match.index)) + onMatch(match);
    regex.lastIndex = end;
    last = end;
  }
  return html + escapeHtml(text.slice(last));
}

// ------------------------------------------------------------------ values

const VALUE = /("(?:[^"\\]|\\.)*"?)|('[^']*'?)|((?:^|\s)#.*$)|\b(true|false|null|nil|none|None|True|False|inf|nan)\b|(-?\b\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?(?:[-T:.\dZ+]*\d)?\b)|([[\]{},])/g;

function values(text) {
  return scan(text, VALUE, (m) => {
    if (m[1] !== undefined || m[2] !== undefined) return span("tk-str", m[0]);
    if (m[3] !== undefined) {
      const lead = m[3].match(/^\s*/)[0];
      return escapeHtml(lead) + span("tk-cmt", m[3].slice(lead.length));
    }
    if (m[4] !== undefined || m[5] !== undefined) return span("tk-num", m[0]);
    return span("tk-punct", m[0]);
  });
}

// ------------------------------------------------------------------ YAML

const YAML_KEY = /^(\s*)(-\s+)?((?:"[^"]*"|'[^']*'|[^\s:#][^:#]*?))(\s*:)(?=\s|$)(.*)$/;

// YAML with block and multi-line plain scalars: after `key: >` (or a plain value),
// more-indented lines are scalar text, not keys. State "" or "s<indent>".
function yamlLine(line, state = "") {
  if (state) {
    const indent = Number(state.slice(1));
    const lead = /^\s*/.exec(line)[0].length;
    if (!line.trim() || lead > indent) return { html: escapeHtml(line), state };
  }
  if (/^\s*#/.test(line)) return { html: span("tk-cmt", line), state: "" };
  const key = YAML_KEY.exec(line);
  if (key) {
    const [, indent, dash = "", name, colon, rest] = key;
    const block = /^\s*[|>][-+]?\s*$/.test(rest);
    const value = block ? span("tk-punct", rest) : yamlValue(rest);
    const depth = indent.length + dash.length;
    return {
      html: escapeHtml(indent) + span("tk-punct", dash) + span("tk-key", name) + span("tk-punct", colon) + value,
      state: block || (rest.trim() && !/^\s*["'[{]/.test(rest)) ? `s${depth}` : "",
    };
  }
  const item = /^(\s*)(-\s+)(.*)$/.exec(line);
  if (item) return { html: escapeHtml(item[1]) + span("tk-punct", item[2]) + yamlValue(item[3]), state: "" };
  return { html: escapeHtml(line), state: "" };
}

function yamlValue(text) {
  // Plain scalars stay text coloured; quoted strings, numbers and comments get colour.
  if (/^\s*["'\d\-[{]|^\s*(true|false|null)\s*$/.test(text) || /\s#/.test(text)) return values(text);
  return escapeHtml(text);
}

function yaml(line, state) {
  return yamlLine(line, state);
}

// ------------------------------------------------------------------ Markdown

const INLINE = new RegExp([
  "(`+)([^`]|[^`][\\s\\S]*?[^`])\\1(?!`)", // 1-2 code span
  "(!?\\[)([^\\]\\n]*)(\\]\\()([^)\\s]*)((?:\\s+\"[^\"]*\")?\\))", // 3-7 link
  "<(https?:\\/\\/[^>\\s]+)>", // 8 autolink
  "(\\*\\*|__)(?=\\S)(.+?\\S)\\9", // 9-10 bold
  "(?<![\\w*])(\\*)(?![\\s*])([^*\\n]+?)(?<!\\s)\\*(?!\\*)", // 11-12 italic *
  "(?<![\\w_])(_)(?![\\s_])([^_\\n]+?)(?<!\\s)_(?![\\w_])", // 13-14 italic _
  "(<!--[\\s\\S]*?-->)", // 15 html comment
  "(https?:\\/\\/[^\\s)<>\"]+)", // 16 bare url
].join("|"), "g");

function inline(text) {
  return scan(text, INLINE, (m) => {
    if (m[1] !== undefined) return span("tk-inl", m[0]);
    if (m[3] !== undefined) {
      return span("tk-punct", m[3]) + `<span class="tk-link">${inline(m[4])}</span>`
        + span("tk-punct", m[5]) + span("tk-url", m[6]) + span("tk-punct", m[7]);
    }
    if (m[8] !== undefined) return span("tk-url", m[0]);
    if (m[9] !== undefined) return `<span class="tk-b">${span("tk-punct", m[9])}${inline(m[10])}${span("tk-punct", m[9])}</span>`;
    if (m[11] !== undefined) return `<span class="tk-i">${span("tk-punct", "*")}${escapeHtml(m[12])}${span("tk-punct", "*")}</span>`;
    if (m[13] !== undefined) return `<span class="tk-i">${span("tk-punct", "_")}${escapeHtml(m[14])}${span("tk-punct", "_")}</span>`;
    if (m[15] !== undefined) return span("tk-cmt", m[0]);
    return span("tk-url", m[0]);
  });
}

function markdown(line, state) {
  if (state === "start") {
    if (line === "---") return { html: span("tk-punct", line), state: "fm", cls: "fm" };
    state = "body";
  }
  if (state === "fm" || state.startsWith("fm|")) {
    if (line === "---" || line === "...") return { html: span("tk-punct", line), state: "body", cls: "fm" };
    const result = yamlLine(line, state.slice(3));
    return { html: result.html, state: result.state ? `fm|${result.state}` : "fm", cls: "fm" };
  }
  if (state.startsWith("code:")) {
    const fence = state.slice(5);
    const close = new RegExp(`^\\s{0,3}${fence[0] === "`" ? "`" : "~"}{${fence.length},}\\s*$`);
    if (close.test(line)) return { html: span("tk-punct", line), state: "body", cls: "code" };
    return { html: span("tk-code", line), state, cls: "code" };
  }
  const open = /^(\s{0,3})(`{3,}|~{3,})(.*)$/.exec(line);
  if (open && !(open[2][0] === "`" && open[3].includes("`"))) {
    return {
      html: escapeHtml(open[1]) + span("tk-punct", open[2]) + span("tk-lang", open[3]),
      state: `code:${open[2]}`,
      cls: "code",
    };
  }
  const heading = /^(\s{0,3})(#{1,6})(\s+.*|)$/.exec(line);
  if (heading) {
    return { html: `<span class="tk-h">${escapeHtml(heading[1])}${span("tk-punct", heading[2])}${inline(heading[3])}</span>`, state: "body" };
  }
  if (/^\s{0,3}([-*_])(\s*\1){2,}\s*$/.test(line)) return { html: span("tk-punct", line), state: "body" };
  const quote = /^(\s{0,3}>+\s?)(.*)$/.exec(line);
  if (quote) return { html: `<span class="tk-quote">${span("tk-punct", quote[1])}${inline(quote[2])}</span>`, state: "body" };
  const list = /^(\s*)([-*+]|\d{1,9}[.)])(\s+)(\[[ xX]\]\s)?(.*)$/.exec(line);
  if (list) {
    return {
      html: escapeHtml(list[1]) + span("tk-list", list[2]) + escapeHtml(list[3]) + span("tk-list", list[4] || "") + inline(list[5]),
      state: "body",
    };
  }
  return { html: inline(line), state: "body" };
}

// ------------------------------------------------------------------ TOML

function toml(line, state) {
  if (state) {
    const end = line.indexOf(state);
    if (end < 0) return { html: span("tk-str", line), state };
    const cut = end + state.length;
    return { html: span("tk-str", line.slice(0, cut)) + values(line.slice(cut)), state: "" };
  }
  if (/^\s*#/.test(line)) return { html: span("tk-cmt", line), state: "" };
  const table = /^(\s*)(\[\[?[^\]]*\]\]?)(.*)$/.exec(line);
  if (table) return { html: escapeHtml(table[1]) + span("tk-h", table[2]) + values(table[3]), state: "" };
  const pair = /^(\s*)((?:"[^"]*"|'[^']*'|[A-Za-z0-9_.\-]+)(?:\s*\.\s*(?:"[^"]*"|'[^']*'|[A-Za-z0-9_\-]+))*)(\s*=\s*)(.*)$/.exec(line);
  if (!pair) return { html: values(line), state: "" };
  const [, indent, key, eq, rest] = pair;
  const head = escapeHtml(indent) + span("tk-key", key) + span("tk-punct", eq);
  const multi = /^("""|''')/.exec(rest);
  if (multi) {
    const close = rest.indexOf(multi[1], 3);
    if (close < 0) return { html: head + span("tk-str", rest), state: multi[1] };
    const cut = close + 3;
    return { html: head + span("tk-str", rest.slice(0, cut)) + values(rest.slice(cut)), state: "" };
  }
  return { html: head + values(rest), state: "" };
}

// ------------------------------------------------------------------ JSON

const JSON_TOKEN = /("(?:[^"\\]|\\.)*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)|([[\]{},])/g;

function json(line) {
  return {
    html: scan(line, JSON_TOKEN, (m) => {
      if (m[1] !== undefined) return m[2] !== undefined ? span("tk-key", m[1]) + span("tk-punct", m[2]) : span("tk-str", m[1]);
      if (m[3] !== undefined || m[4] !== undefined) return span("tk-num", m[0]);
      return span("tk-punct", m[0]);
    }),
    state: "",
  };
}

// ------------------------------------------------------------------ scripts

const SCRIPT = /("(?:[^"\\]|\\.)*"?)|('(?:[^'\\]|\\.)*'?)|((?:^|\s)#.*$)|\b(\d+(?:\.\d+)?)\b/g;

function script(line) {
  if (/^#!/.test(line)) return { html: span("tk-cmt", line), state: "" };
  return {
    html: scan(line, SCRIPT, (m) => {
      if (m[1] !== undefined || m[2] !== undefined) return span("tk-str", m[0]);
      if (m[3] !== undefined) {
        const lead = m[3].match(/^\s*/)[0];
        return escapeHtml(lead) + span("tk-cmt", m[3].slice(lead.length));
      }
      return span("tk-num", m[0]);
    }),
    state: "",
  };
}

const plain = (line) => ({ html: escapeHtml(line), state: "" });

export const LANGUAGES = {
  markdown: { label: "Markdown", initial: "start", line: markdown },
  toml: { label: "TOML", initial: "", line: toml },
  json: { label: "JSON", initial: "", line: json },
  yaml: { label: "YAML", initial: "", line: yaml },
  script: { label: "Script", initial: "", line: script },
  plain: { label: "Plain text", initial: "", line: plain },
};

export function languageFor(path) {
  const ext = (/\.([^./]+)$/.exec(path || "") || [])[1]?.toLowerCase();
  if (ext === "md" || ext === "markdown") return "markdown";
  if (ext === "toml") return "toml";
  if (ext === "json") return "json";
  if (ext === "yaml" || ext === "yml") return "yaml";
  if (ext === "sh" || ext === "py" || ext === "bash" || ext === "zsh") return "script";
  return "plain";
}

// Keeps per-line HTML and state, and patches only the changed lines.
export function createHighlightModel() {
  let language = LANGUAGES.plain;
  let lines = [];
  let states = [];
  let rows = [];

  const entry = (index) => (index > 0 ? states[index - 1] : language.initial);

  function run(source, from, state) {
    const out = [];
    for (const line of source) {
      const result = language.line(line, state);
      state = result.state;
      out.push(result);
    }
    return out;
  }

  return {
    setLanguage(name) {
      language = LANGUAGES[name] || LANGUAGES.plain;
    },
    label: () => language.label,
    // Full render: returns every row.
    reset(text) {
      lines = text.split("\n");
      rows = run(lines, 0, language.initial);
      states = rows.map((row) => row.state);
      return rows;
    },
    // Incremental render: returns { start, removed, rows } for the DOM patch.
    update(text) {
      const next = text.split("\n");
      const limit = Math.min(lines.length, next.length);
      let start = 0;
      while (start < limit && lines[start] === next[start]) start += 1;
      let tail = 0;
      while (tail < limit - start && lines[lines.length - 1 - tail] === next[next.length - 1 - tail]) tail += 1;
      let state = entry(start);
      const fresh = [];
      let index = start;
      const endNew = next.length - tail;
      for (; index < endNew; index += 1) {
        const result = language.line(next[index], state);
        state = result.state;
        fresh.push(result);
      }
      let oldIndex = lines.length - tail;
      while (index < next.length && state !== entry(oldIndex)) {
        const result = language.line(next[index], state);
        state = result.state;
        fresh.push(result);
        index += 1;
        oldIndex += 1;
      }
      const removed = oldIndex - start;
      lines = next;
      rows.splice(start, removed, ...fresh);
      states.splice(start, removed, ...fresh.map((row) => row.state));
      return { start, removed, rows: fresh };
    },
    count: () => lines.length,
  };
}
