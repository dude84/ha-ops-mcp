# Changelog — HA Ops Design System

All notable changes to this design system. Versions are date-stamped; the system
follows the deliverables in `docs/DESIGN_SYSTEM_BRIEF.md`.

---

## [1.1] — 2026-05-31 · Brand mark

Net-new brand work beyond the original functional-tool brief (the brief explicitly
scoped brand identity *out* — this was added at the user's request).

### Added — logo / icon system
- **New mark**: a robot hand gripping a **cone drill bit** (with angled flutes), a clean
  vector redraw of the original add-on icon. Keeps the original DNA — four indigo
  knuckles, green chip with pin-dots, dotted vents, ribbed cylindrical wrist — in the
  original cyan / indigo / green palette with a navy outline.
- **Master vector**: `assets/logo.svg`.
- **Transparent PNG**: `assets/logo-128.png`, `logo-256.png`, `logo-512.png`.
- **White-flattened PNG** (for surfaces without alpha): `assets/logo-white-128.png`,
  `logo-white-256.png`, `logo-white-512.png`.
- **Favicon**: `assets/favicon.svg` + `favicon-16.png`, `favicon-32.png`, `favicon-64.png`
  (flat two-tone cut for tiny sizes).
- **Wordmark lockup**: `assets/wordmark.svg` / `wordmark.png` (navy, light bg) and
  `wordmark-dark.svg` / `wordmark-dark.png` (white, dark bg) — mark + mono `ha-ops`.
- **Exploration board**: `Logo Exploration.html` (4 directions — Faithful, app tile,
  Simplified, Flat favicon). Direction **1 · Faithful** was selected.

### Changed
- **Live add-on icon swapped**: `assets/logo.png` + `assets/icon.png` now carry the new
  mark (256×256 transparent — drop-in compatible with the originals).
- **UI kit header** (`ui_kits/sideload-ui/`) now shows the mark beside the "HA Ops"
  wordmark (previously text-only).
- **UI kit favicon**: `<link rel="icon">` tags added to `ui_kits/sideload-ui/index.html`.
- **Docs updated**: README Iconography + file index, and the `Mark & wordmark` spec card,
  now describe the real mark instead of the old stock icon.

### Design-system cards added
- `Logo exploration` (Brand), `Wordmark lockup` (Brand).

### Notes / caveats
- The mark is a **redraw of the existing concept**, not bespoke-from-scratch identity.
- Open: whether to use the wide `wordmark.png` as the HA `logo.png` (convention) vs. the
  square mark for both (matches the original repo). Left as the square for now.

---

## [1.0] — 2026-05-31 · Initial release

The documented design system for the ha-ops-mcp sideload admin UI. Built from the real
source (`src/ha_ops_mcp/static/ui.html`, `safety/classification.py`) per the brief.

### Added — foundations
- **Two-tier token system** (`colors_and_type.css`): primitive ramps → semantic tokens,
  with light (`:root`) + dark (`.dark`) values for every token.
- **Op-class risk language** replacing the four hand-rolled `sev-*` classes:
  `--op-read` (gray) · `--op-mutate` (amber) · `--op-destructive` (red), plus
  `--state-ok` / `--state-fail` and a scoped diff-syntax palette.
- **Type / spacing / radii / shadow scales** formalized from the Tailwind defaults in use;
  semantic type roles (`.ds-wordmark`, `.ds-stat`, `.ds-mono`, …).

### Added — area visual language
- **Self-hosted Lucide (ISC) icon sprite** `assets/area-icons.svg` — 15 subsystem areas
  + `misc` catch-all (mirrors the `("mutate","misc")` fallback in `classification.py`),
  with per-area files in `assets/area-icons/`. Text `·area·` tag stays the accessible
  baseline; icons are additive.

### Added — documentation
- `README.md` — context, CONTENT FUNDAMENTALS, VISUAL FOUNDATIONS, ICONOGRAPHY, file index.
- `IMPLEMENTATION.md` — buildless wiring (inline `tailwind.config` mapping) + the
  `sev-*` → semantic migration table.
- `RELEASE_BRIEF_v1.0.md` — dev-team handoff: scope, steps, acceptance criteria.
- `SKILL.md` — Agent-Skill manifest for downloadable use.

### Added — specimens & reference
- **20 spec cards** in `preview/` across Type / Colors / Spacing / Components / Brand.
- **Interactive UI kit** `ui_kits/sideload-ui/` — high-fidelity React recreation of the
  Timeline / Backups / Health panel, light + dark, with reusable components.

### Constraints honored
- Buildless · offline (system fonts, self-hosted assets) · dark-mode mandatory ·
  color never the sole signal (text labels on pills).
