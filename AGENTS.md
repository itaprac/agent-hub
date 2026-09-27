## Agent skills

### Issue tracker

Issues are tracked in this repository's GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

Triage uses the five default Matt Pocock skill labels. See `docs/agents/triage-labels.md`.

### Domain docs

This repository uses a single-context layout. See `docs/agents/domain.md`.

## Console layouts (trial period)

The Console currently ships two complete front ends over the same API, so the operator can compare them:

- **Workbench** at `/`: `web/` (design in `DESIGN.md`).
- **Editorial** at `/editorial/`: `web/editorial/` (design in `DESIGN-editorial.md`; frontend tests `tests/*_editorial.mjs`).

The operator switches between them with the layout button or `M`; the choice is remembered in `localStorage` (`web/js/layout.js`, `web/js/layout-redirect.js`).
Before any frontend change, ask the operator whether it should go into both layouts or only one, and whether one layout should be dropped instead.
