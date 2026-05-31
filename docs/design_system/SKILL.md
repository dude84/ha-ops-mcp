---
name: ha-ops-design
description: Use this skill to generate well-branded interfaces and assets for HA Ops (the ha-ops-mcp sideload admin UI), either for production or throwaway prototypes/mocks/etc. Contains essential design guidelines, colors, type, fonts, assets, and UI kit components for prototyping a dense, dark-mode-first, offline functional admin tool for Home Assistant power users.
user-invocable: true
---

Read the README.md file within this skill, and explore the other available files.

If creating visual artifacts (slides, mocks, throwaway prototypes, etc), copy assets out
and create static HTML files for the user to view. If working on production code, you can
copy assets and read the rules here to become an expert in designing with this brand.

If the user invokes this skill without any other guidance, ask them what they want to
build or design, ask some questions, and act as an expert designer who outputs HTML
artifacts _or_ production code, depending on the need.

## Quick map
- `README.md` — product context, content & visual foundations, iconography, file index.
- `colors_and_type.css` — the tokens. Two-tier (primitive → semantic) colors, type,
  spacing, radii, shadow; light + dark. **Reference these, never invent hex.**
- `ui_kits/sideload-ui/` — interactive recreation of the admin panel + reusable JSX
  components (Header, TimelineTab, BackupsTab, HealthTab, Badge, DiffView).
- `preview/` — small specimen cards for every token group.
- `assets/` — the add-on icon (`logo.png` / `icon.png`).

## Non-negotiables when designing for HA Ops
1. **Dark mode is a peer, not a filter** — every token has a light + dark value; design both.
2. **Offline** — system font stack only, no webfonts, no icon-font CDN. Self-host any SVG.
3. **Color = risk + state** — gray neutral canvas, blue is the only accent. READ=gray,
   MUTATE=amber, DELETE=red, ok=green, fail=red. Spend color deliberately.
4. **Color is never the sole signal** — op-class pills always carry their text label.
5. **Dense & utilitarian** — small type (xs/sm), monospace for machine text, flat shadows,
   two radii, borders over elevation. No gradients, no glass, no decorative imagery.
6. **Voice** — operator-to-operator: precise, calm, candid about irreversibility. Sentence
   case labels, UPPERCASE op-class pills, verbatim monospace for tools/paths/tokens.

Source of truth: https://github.com/dude84/ha-ops-mcp
