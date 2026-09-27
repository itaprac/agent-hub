---
name: agent-hub
description: A calm, editorial console for shared agent state across trusted machines.
colors:
  dark-canvas: "#17150f"
  dark-panel: "#1e1c17"
  dark-sunken: "#110f0b"
  dark-text: "#eae8e1"
  dark-muted: "#b7b4aa"
  dark-dim: "#9d9b95"
  dark-copper: "#e4ac59"
  dark-copper-ink: "#23190a"
  black-canvas: "#000000"
  black-panel: "#100f0c"
  black-sunken: "#070604"
  light-canvas: "#f5f3ed"
  light-panel: "#fdfcf8"
  light-sunken: "#edebe4"
  light-text: "#1d1b10"
  light-muted: "#4b483c"
  light-dim: "#636057"
  light-copper: "#a75c00"
  light-copper-ink: "#faf8f1"
  success-dark: "#6fd087"
  warning-dark: "#e8be62"
  danger-dark: "#f47b74"
  success-light: "#197037"
  warning-light: "#865900"
  danger-light: "#ac3031"
typography:
  display:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Segoe UI, system-ui, sans-serif"
    fontSize: "40px"
    fontWeight: 650
    lineHeight: 1.08
    letterSpacing: "-0.035em"
  section:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Segoe UI, system-ui, sans-serif"
    fontSize: "20px"
    fontWeight: 620
    lineHeight: 1.25
    letterSpacing: "-0.02em"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, SF Pro Text, Segoe UI, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  code:
    fontFamily: "ui-monospace, SF Mono, SFMono-Regular, Menlo, Monaco, Consolas, monospace"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: "22px"
    letterSpacing: "normal"
rounded:
  control: "999px"
  row: "14px"
  card: "16px"
  sheet: "20px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "28px"
  section: "64px"
---

# Design System: agent-hub

## Overview

**Creative North Star: "Calm Editorial"**

The Console reads like a well-set page. A light, borderless sidebar sits on the canvas; content is one centred column (max 960px) under a 40px sentence-case title. Every page opens with a plain statement of real state (for example "Everything is in sync." or "1 machine needs attention.") and a quiet sub-line of evidence. Light is the showcase; Dark, Black and System are equal. System keeps the stored default (Dark) and maps OS dark to Dark, never Black.

Canonical colour values are OKLCH in `web/style.css`; the frontmatter holds sRGB exports.

## Colour

- **Canvas / panel / sunken**: warm olive paper (Light) or ink (Dark). Panels are one step lighter than the canvas; sunken fills sit behind icons, chips, segmented controls and the editor.
- **Copper** (`--accent`): primary actions (Sync, New skill, Save), focus rings, current selection (selected card, switch on, dirty dot). Keep it under 10% of a screen.
- **Green / yellow / red**: real state only (machine state, check results, problems, errors, the failed-command dot on the Log button). Never decoration.
- **Series colours** (`--usage-codex`, `--usage-claude`, `--usage-grok`, `--usage-cursor`): charts, tool shares and swatches only. Cursor is violet, not green, so it cannot read as "healthy".
- **Syntax colours** (`--syn-*`): editor only. Headings coral, keys blue, strings teal, numbers magenta, inline code rose, links blue. They avoid the state hues and meet AA on the editor surface.
- **Black**: pure black only on page and sidebar canvas (`docs/adr/0001-black-canvas-pure-black.md`). Panels keep the olive tint.

## Typography

- System sans for everything a person reads: titles, labels, buttons, sentences. Sentence case; no uppercase letter-spaced labels.
- Mono only for paths, hashes, commands, model ids and editor text.
- Scale: 40px page titles (32px below 720px), 76px usage figure (52px mobile), 20px section titles, 15px body, 13–13.5px supporting text.
- Tabular figures for money, token counts and times.

## Layout

- **Sidebar**: 232px, no border, labelled tabs with shortcut hints on hover. Footer holds colour scheme, Refresh, Log and Settings icon buttons and the machine identity.
- **Below 1100px**: 76px icon rail; labels move to tooltips and accessible names.
- **Below 720px**: bottom tab bar with all five destinations (icon + short label), and a top bar with the brand and the four tool buttons. Nothing is hidden: Refresh, theme, Log, Settings, Sync, Apply, filters and editor actions stay reachable at 320px.
- Sections are separated by 64px of space, not rules. Tables and wide lists scroll inside their section.

## Components

- **Buttons**: pill shaped, 34px (48px for the hero Sync); ghost for secondary, copper for primary, red text for destructive. 44px on narrow or coarse-pointer screens.
- **Segmented control**: sunken pill with a raised panel for the pressed option (`aria-pressed`).
- **Machine rows**: 52px device tile, name + host, state word with dot, "Confirmed Xm ago", chevron. Rows expand in place (grid-rows transition) to facts, command buttons and the dry-run switch.
- **Grouped lists** (iOS-Settings style): rounded panel, 50px rows, inset separators, tinted 24px status icon. Status uses "Needs attention" then "Healthy" groups.
- **Cards**: skill grid, 16px radius, hover lifts 2px with a stronger border; badges for problems, Disabled, target limits and Installed.
- **Sheet**: right-side drawer (full screen below 720px) with the item's title, state, actions, file list and editor. It makes the page inert while open and returns focus to its card on close (Esc).
- **Editor**: transparent-text `<textarea>` over a highlighted `<pre>` in one grid cell with identical font, padding and wrapping, so the textarea stays the real input (undo, selection, IME, screen readers). Line numbers and the current-line tint live in the overlay; only changed lines are re-highlighted.
- **Log**: popover from the sidebar (L), not a permanent bar.
- **Toasts**: bottom-centre pills; errors use the red fill.

## Elevation and motion

Flat by default. One soft shadow (`--shadow`) is reserved for floating layers: sheet, popovers (log, theme menu), chart tooltip, dialogs and toasts. Motion is short and eased (rise-in views, expanding rows, sliding sheet); `prefers-reduced-motion` removes it.

## Accessibility

WCAG 2.2 AA contrast for text roles in all four schemes, visible 2px copper focus rings, keyboard access to every control (tabs with arrow keys, 1–5, R, L, /, ⌘S, Esc, chart arrow keys), 44px targets on touch, and the live `role="status"` regions of the previous design.

## Don'ts

No generic SaaS dashboards, terminal cosplay, neon, glassmorphism, gradient text, pure white, or coloured side stripes. No shadows on resting cards. Don't use state colours for decoration.
