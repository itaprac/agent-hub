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
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace"
    fontSize: "11px"
    fontWeight: 600
    lineHeight: 1.45
    letterSpacing: "0.14em"
rounded:
  xs: "6px"
  sm: "8px"
  md: "10px"
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
    rounded: "{rounded.md}"
    padding: "16px 18px 14px"
  machine-surface-light:
    backgroundColor: "{colors.light-surface}"
    textColor: "{colors.light-text}"
    rounded: "{rounded.md}"
    padding: "16px 18px 14px"
---

# Design System: agent-hub

## Overview

**Creative North Star: "The Maintainer's Workbench"**

agent-hub is a compact work surface for focused repository maintenance. Its visual hierarchy is quiet, technical, and evidence-led. Warm olive neutrals reduce glare, copper marks deliberate primary actions, and semantic colors report real state only.

The desktop layout stays dense for repeated operation. At 1000px and below, interactive targets become 44px high. At 600px and below, navigation moves to a full-width top rail, workspaces stack, editor actions receive their own row, and wide data tables scroll inside their section. Dark, Light, Black, and System modes are equal operating environments.

**Key Characteristics:**

- Restrained color with one copper action accent.
- System sans for controls and system mono for state, paths, and data.
- Tonal layers and one-pixel borders before shadows.
- Structural responsive changes at 1000px, 860px, and 600px.
- Visible focus, reduced motion support, and 44px touch targets on narrow or coarse-pointer devices.

## Colors

The palette is warm olive ink and paper, with copper reserved for actions and selection. The frontmatter values are sRGB exports for Stitch. Canonical implementation values remain OKLCH in `web/style.css` and in the sidecar metadata.

### Primary

- **Workbench Copper** (`dark-copper`, `light-copper`): primary buttons, focus rings, active file metadata, and loading progress.

### Secondary

- **Verified Green** (`success-dark`, `success-light`): healthy machine and successful operation state only.
- **Attention Ochre** (`warning-dark`, `warning-light`): drift, unsaved work, or incomplete state only.
- **Failure Red** (`danger-dark`, `danger-light`): errors, destructive actions, and failed state only.

### Neutral

- **Olive Ink Canvas** (`dark-canvas`, `dark-rail`, `dark-surface`, `dark-sunken`): Dark colour scheme page, navigation, panels, and recessed editor areas.
- **Olive Paper Canvas** (`light-canvas`, `light-rail`, `light-surface`, `light-sunken`): the equivalent Light colour scheme layers.
- **Black Canvas** (`black-canvas`, `black-rail`, `black-surface`, `black-sunken`): Black colour scheme page and rail. Pure black is allowed here only. Cards, panels, and recessed surfaces stay slightly lighter and keep the olive tint. See `docs/adr/0001-black-canvas-pure-black.md`.
- **Evidence Text** (`dark-text`, `light-text`): headings, values, and operational verdicts.
- **Supporting Text** (`dark-muted`, `dark-faint`, `dark-dim`, `light-muted`, `light-faint`, `light-dim`): three accessible hierarchy levels. The weakest level remains at least 4.5:1 against its hardest intended surface.

### Named Rules

**The Evidence Color Rule.** Green, yellow, and red must encode machine or operation state. They must never decorate neutral content.

**The Copper Restraint Rule.** Copper is limited to primary actions, focus, current selection, and loading. It must remain below 10% of a screen.

**The Black Canvas Rule.** Only the Black colour scheme may use pure black, and only on the page and rail canvas. Cards, panels, and recessed surfaces stay slightly lighter with the olive tint. Copper and status colors do not change. System never resolves to Black.

## Typography

**Display Font:** System sans (`headline`)
**Body Font:** System sans (`body`)
**Label/Mono Font:** System monospace (`code`, `label`)

**Character:** Native system type keeps controls familiar and fast. Monospace text identifies paths, commands, status metadata, and editable content without turning the product into decorative terminal cosplay.

### Hierarchy

- **Headline** (`headline`): page titles only. It steps down from 28px to 24px below 860px and to 30px only for the narrow usage total.
- **Title** (`title`): machine names, modal titles, and strong empty-state labels.
- **Body** (`body`): controls and explanations. Prose must stay within 65 to 75 characters per line when a prose block exists.
- **Code** (`code`): editor content and logs. Preserve a relaxed 1.65 line height for scanning.
- **Label** (`label`): short uppercase section labels and metadata. Do not use it for sentences.

### Named Rules

**The Evidence Type Rule.** Sans explains the interface. Mono shows state, identity, commands, paths, and data.

## Elevation

The interface is flat by default. Depth comes from adjacent tonal layers and one-pixel borders. The single ambient shadow belongs to floating dialogs, toasts, and chart tooltips. Small semantic glows may reinforce status dots in dark mode, but they must not spread to cards or controls.

### Shadow Vocabulary

- **Floating Surface** (`shadow`): a broad, low-edge shadow for dialogs, toasts, and tooltips only.
- **Status Glow** (`glow-ok`, `glow-warn`, `glow-bad`): a compact dark-mode halo around a state dot. Light mode disables it.

### Named Rules

**The Flat Workbench Rule.** Resting panels have no shadow. If a normal card looks lifted, remove the shadow and restore tonal separation.

## Components

Components use familiar shapes, direct labels, visible state changes, and compact desktop dimensions.

### Buttons

- **Shape:** gently curved (`sm`), 32px high on pointer-precise desktop and at least 44px high at narrow widths or with a coarse pointer.
- **Primary:** Workbench Copper with matching ink text and 12px horizontal padding.
- **Hover / Focus:** hover preserves the AA color pair and adds an ink-colored border; focus uses a two-pixel copper outline with a two-pixel offset.
- **Secondary / Danger:** transparent neutral buttons use one-pixel borders. Danger uses Failure Red for text and border, with a soft red hover surface.

### Chips

- **Style:** pill radius, one-pixel border, semantic dot, and compact mono label.
- **State:** color names real state. A chip must not rely on color alone; its text carries the verdict.

### Cards / Containers

- **Corner Style:** gently curved (`md`).
- **Background:** surface tokens separate content from the canvas.
- **Shadow Strategy:** flat at rest.
- **Border:** one pixel only. Colored side stripes greater than one pixel are prohibited.
- **Internal Padding:** 16px to 18px on desktop and 12px to 14px below 600px.

### Inputs / Fields

- **Style:** recessed surface, one-pixel neutral border, `sm` radius, and mono content.
- **Focus:** two-pixel copper outline with a one-pixel offset.
- **Error / Disabled:** `aria-invalid` fields keep a two-pixel Failure Red focus ring and error copy uses the same semantic color. Disabled controls may reduce opacity because WCAG contrast does not apply to inactive controls.

### Navigation

- **Desktop:** a 236px left rail with labeled tabs. It collapses to a 68px icon rail below 1000px.
- **Mobile:** below 600px, five equal 44px navigation targets form a top rail. No destination is removed.
- **State:** inactive tabs use supporting text. Hover and selection use a neutral tonal fill; selection does not depend on a colored stripe.

### Editor

- **Desktop:** file path, state flag, and actions share a 46px header.
- **Mobile:** the path and state occupy the first row; Revert, Delete, and Save occupy three equal 44px targets on the second row.
- **Content:** the editor is a recessed mono surface. Its footer keeps cursor position, size, and save hint visible.

## Do's and Don'ts

### Do:

- **Do** show repository state and consequences with text, counts, and exact paths.
- **Do** keep desktop controls compact, then expand interactive targets to at least 44px on narrow or coarse-pointer devices.
- **Do** preserve all five navigation destinations at 320px, 375px, and 768px.
- **Do** use one-pixel borders, tonal surfaces, and the documented radius scale.
- **Do** keep every small active text role at WCAG 2.2 AA contrast.

### Don't:

- **Don't** build generic SaaS dashboards.
- **Don't** use decorative terminal cosplay.
- **Don't** use neon-on-black AI tooling.
- **Don't** use glassmorphism.
- **Don't** use metric-heavy hero layouts.
- **Don't** add visual effects that compete with repository state.
- **Don't** add custom scrollbars, pure white, gradient text, or colored side stripes greater than one pixel.
- **Don't** use pure black except the Black colour scheme page and rail canvas.
- **Don't** hide Refresh, theme selection, navigation, editor actions, filtering, Apply, or Sync to make a narrow layout fit.
