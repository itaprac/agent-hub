# Domain docs

Engineering skills must read the relevant domain documentation before exploring the codebase.

## Files to read

- Read `CONTEXT.md` at the repository root if it exists.
- Read relevant ADRs under `docs/adr/` if they exist.
- Proceed silently when these files do not exist.

The `/domain-modeling` skill creates these files when the project resolves domain terms or architectural decisions.

## File structure

This repository uses a single-context layout:

```text
/
├── CONTEXT.md
├── docs/adr/
│   ├── 0001-example-decision.md
│   └── 0002-another-decision.md
└── agenthub/
```

## Use glossary terms

Use terms defined in `CONTEXT.md` in issue titles, proposals, hypotheses, tests, and code.

If a required concept is absent, check whether the term is unnecessary or whether the domain model needs an update.

## Report ADR conflicts

State when proposed work conflicts with an existing ADR. Do not override the decision silently.
