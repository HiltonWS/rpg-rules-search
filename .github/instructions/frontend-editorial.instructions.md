---
name: "Frontend Editorial"
description: "Use when editing Arquivo Arcano templates, JavaScript, or CSS. Covers Portuguese UI, safe text rendering, taxonomy colors, PDF preview, and responsive validation."
applyTo: "src/rpg_rules_search/templates/**, src/rpg_rules_search/static/**"
---

# Frontend Editorial

- Preserve the existing editorial, utilitarian visual language and Portuguese interface text.
- Build result text with DOM text nodes; do not insert library content through `innerHTML`.
- Keep search highlighting and RPG taxonomy highlighting composable and Unicode-aware.
- New controls need labels, loading states, and Portuguese error messages.
- Preserve selectable PDF text and responsive preview controls.
- Validate a real interaction at desktop and mobile sizes; check horizontal overflow and text overlap.
- Do not edit `*.min.*` by hand.