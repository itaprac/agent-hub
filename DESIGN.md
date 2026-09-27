---
name: agent-hub
description: A precise operator console for shared agent state across trusted machines.
colors:
  dark-canvas: "#17150f"
  dark-rail: "#12100b"
  dark-surface: "#1d1c17"
  dark-sunken: "#100e09"
  dark-text: "#eae8e1"
  dark-muted: "#adaba1"
  dark-faint: "#a4a299"
  dark-dim: "#9a9891"
  dark-copper: "#e4ac59"
  dark-copper-ink: "#23190a"
  black-canvas: "#000000"
  black-rail: "#000000"
  black-surface: "#12100d"
  black-sunken: "#060503"
  light-canvas: "#f4f2ea"
  light-rail: "#edebe2"
  light-surface: "#fdfcf7"
  light-sunken: "#efede5"
  light-text: "#1d1b10"
  light-muted: "#4b483c"
  light-faint: "#555349"
  light-dim: "#5d5b53"
  light-copper: "#a75c00"
  light-copper-ink: "#faf8f1"
  success-dark: "#6fd087"
  warning-dark: "#e8be62"
  danger-dark: "#ed756e"
  success-light: "#197037"
  warning-light: "#865900"
  danger-light: "#ac3031"
typography:
  headline:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "28px"
    fontWeight: 600
    lineHeight: 1.08
    letterSpacing: "-0.028em"
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "18px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.45
    letterSpacing: "normal"
  code:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.65
    letterSpacing: "normal"
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, Segoe UI, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 500
    lineHeight: 1.4
    letterSpacing: "normal"
rounded:
  xs: "8px"
  sm: "10px"
  md: "12px"
  card: "14px"
  canvas: "16px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "28px"
components:
  button-primary-dark:
    backgroundColor: "{colors.dark-copper}"
    textColor: "{colors.dark-copper-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "0 12px"
    height: "32px"
  button-primary-light:
    backgroundColor: "{colors.light-copper}"
    textColor: "{colors.light-copper-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.sm}"
    padding: "0 12px"
    height: "32px"
  field-dark:
    backgroundColor: "{colors.dark-sunken}"
    textColor: "{colors.dark-text}"
    typography: "{typography.code}"
    rounded: "{rounded.sm}"
    padding: "0 11px"
    height: "36px"
  field-light:
    backgroundColor: "{colors.light-sunken}"
    textColor: "{colors.light-text}"
    typography: "{typography.code}"
    rounded: "{rounded.sm}"
    padding: "0 11px"
    height: "36px"
  machine-surface-dark:
    backgroundColor: "{colors.dark-surface}"
    textColor: "{colors.dark-text}"
    rounded: "{rounded.card}"
    padding: "20px 22px 18px"
  machine-surface-light:
    backgroundColor: "{colors.light-surface}"
    textColor: "{colors.light-text}"
    rounded: "{rounded.card}"
    padding: "20px 22px 18px"
---

# Design System: agent-hub

## Overview

**Creative North Star: "The Soft Workbench"**

agent-hub is a calm work surface for repository maintenance. The navigation sits directly on the rail colour; all content lives in one inset, rounded canvas panel with a slim header (breadcrumb, Store path pill, Refresh with its `R` hint, colour-scheme menu). Warm olive neutrals reduce glare, copper marks the primary action, focus, and selection, and semantic colours report real state only. The approved reference is `prototypes/a-soft-workbench.html` in the main checkout.

Dark, Light, Black, and System are equal operating environments.

**Key characteristics:**

- Sans-serif, sentence-case interface. Mono only for paths, hashes, numbers in tables, commands, and code. No uppercase letter-spaced labels.
- Cards: 14px radius, a one-pixel ring, and a very soft drop. Floating layers (menus, tooltips, log drawer, dialogs) use the pop shadow.
- Segmented controls are pills with a sliding indicator (`dom.js placeIndicators`).
- Structural responsive changes at 1180px, 1000px (68px icon rail, stacked workspace), 860px, and 600px (top navigation, stacked editor actions).
- Visible copper focus rings, reduced-motion support, 44px targets at ≤1000px or with a coarse pointer.

## Colors

Canonical values are OKLCH in `web/style.css`; the frontmatter holds sRGB exports.

- **Layers:** `--rail` (sidebar and page) → `--canvas` (inset panel) → `--card` / `--card-2` (cards, raised controls) → `--sunken` (inputs, log, gutters). Black keeps rail and canvas at pure black and lifts cards slightly (ADR 0001).
- **Text:** `--text`, `--muted`, `--faint`, `--dim`. The weakest level keeps 4.5:1 on its hardest surface.
- **Copper** (`--accent`): primary button, focus ring, selected tree item, active nav icon, loading bar. Below 10% of a screen.
- **State:** `--green` synced/ok, `--yellow` drift/waiting/unsaved, `--red` errors and destructive actions. Each has a `-soft` fill for badges and row tints.
- **Brands in charts:** `--usage-claude` (orange), `--usage-codex` (neutral ink), `--usage-grok`, `--usage-cursor`.
- **Syntax:** `--syn-key`, `--syn-str`, `--syn-num`, `--syn-link`, `--syn-head`, `--syn-pre`. Warm, low chroma, never state colours.

**The Evidence Color Rule.** Green, yellow, and red encode state only. **The Copper Restraint Rule.** Copper is for action, focus, and selection only. **The Black Canvas Rule.** Pure black only on Black's rail and canvas; System never resolves to Black.

## Typography

- **Headline** 28px/600, −0.028em: page verdicts and page titles (24px below 860px).
- **Title** 15–20px/600: section and workspace titles, machine names, modal titles.
- **Body** 13–14px sans: controls and explanations. Small labels are 12px sans, weight 500, sentence case.
- **Code** 13px/1.7 mono: editor, log, paths, commit hashes, file sizes.

## Components

- **Buttons:** 32px (28px `btn-sm`, 38px `btn-lg`), 10px radius. Primary is copper with ink text. Ghost buttons are transparent until hover. Danger uses red text and a soft red hover.
- **Tags / badges:** 20px pills; neutral ring by default, soft fills for ok, warn, accent, and an outline for off.
- **Status:** verdict header with a state badge, meta line (Store rev, machine count, automatic sync), and Sync. Machine cards show state, confirmation age, a facts strip (commit, last sync, recorded, app), agent chips, dry-run switch, and per-machine Sync / Apply. Local checks are one card, grouped by Agent, with an All / Issues filter that appears only when there are issues.
- **Usage:** hero cost with by-machine bars, per-tool rows with share bars and source warnings, five KPI tiles, smooth area chart (monotone curves, peak label, hover and keyboard crosshair with tooltip), and a breakdown table by model or by period.
- **Workspaces:** 320px tree pane (title with count, actions, filter with `/` hint, grouped tree with icons, badges, provenance) beside the editor card. The context bar above the editor carries item actions (Targets, Disable, Back up…).
- **Editor:** the `<textarea>` stays the real input with transparent text over a highlighted `<pre>` that wraps identically; both grow inside one scroll container, so no scroll syncing is needed. Line numbers are CSS counters in the gutter; the caret line is tinted. Markdown, TOML, JSON, YAML, shell, and Python are highlighted line by line with memoised state; only changed lines are re-rendered, and files above 400 kB show plain text.
- **Log:** a pill at the bottom right (command, exit code, `L`) that expands into a drawer; `L` or Escape closes it.
- **Dialogs, menus, tooltips, toasts:** `--panel` surface with the pop shadow; toasts are inverted pills at the bottom centre.

## Do's and Don'ts

**Do** show state with text, counts, and exact paths; keep all five destinations, Refresh, theme, filtering, editor actions, Apply, and Sync reachable at 320px; keep AA contrast on every small text role; respect reduced motion.

**Don't** use uppercase letter-spaced labels, mono for prose, glassmorphism, neon, gradient text, pure white, colored side stripes wider than one pixel, custom scrollbars, or pure black outside the Black scheme.
