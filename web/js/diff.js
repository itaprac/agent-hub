// Line diff for comparing a backup with the current text.

const MAX_CELLS = 4_000_000;
const CONTEXT = 3;

// Returns [{op: " " | "-" | "+", text}] from `before` to `after`.
export function diffLines(before, after) {
  const a = before.split("\n");
  const b = after.split("\n");
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start += 1;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA -= 1;
    endB -= 1;
  }
  const midA = a.slice(start, endA);
  const midB = b.slice(start, endB);
  const same = (lines) => lines.map((text) => ({ op: " ", text }));
  let middle;
  if (midA.length * midB.length > MAX_CELLS) {
    middle = [...midA.map((text) => ({ op: "-", text })), ...midB.map((text) => ({ op: "+", text }))];
  } else {
    // Longest common subsequence table, filled from the end.
    const width = midB.length + 1;
    const table = new Uint32Array((midA.length + 1) * width);
    for (let i = midA.length - 1; i >= 0; i -= 1) {
      for (let j = midB.length - 1; j >= 0; j -= 1) {
        table[i * width + j] = midA[i] === midB[j]
          ? table[(i + 1) * width + j + 1] + 1
          : Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
      }
    }
    middle = [];
    let i = 0;
    let j = 0;
    while (i < midA.length || j < midB.length) {
      if (i < midA.length && j < midB.length && midA[i] === midB[j]) {
        middle.push({ op: " ", text: midA[i] });
        i += 1;
        j += 1;
      } else if (i < midA.length && (j === midB.length || table[(i + 1) * width + j] >= table[i * width + j + 1])) {
        middle.push({ op: "-", text: midA[i] });
        i += 1;
      } else {
        middle.push({ op: "+", text: midB[j] });
        j += 1;
      }
    }
  }
  return [...same(a.slice(0, start)), ...middle, ...same(a.slice(endA))];
}

// Keeps changed lines with a few lines of context; long unchanged runs become one gap.
export function diffHunks(lines) {
  const keep = lines.map(() => false);
  lines.forEach((line, index) => {
    if (line.op === " ") return;
    for (let k = Math.max(0, index - CONTEXT); k <= Math.min(lines.length - 1, index + CONTEXT); k += 1) keep[k] = true;
  });
  const result = [];
  let gap = 0;
  lines.forEach((line, index) => {
    if (keep[index]) {
      if (gap) result.push({ op: "gap", count: gap });
      gap = 0;
      result.push(line);
    } else {
      gap += 1;
    }
  });
  if (gap) result.push({ op: "gap", count: gap });
  return result;
}
