# HA Ops — Design System

**Version 1.0**

A design system for the **ha-ops-mcp sideload admin UI** — the embedded panel that
ships with [`ha-ops-mcp`](https://github.com/dude84/ha-ops-mcp), an MCP server that
gives AI assistants (and their operators) deep operational access to Home Assistant:
database queries, YAML config editing, Lovelace dashboard management, entity hygiene,
system health, add-on control, and a cross-surface reference graph.

This is **not** a marketing brand. It is the documented, tokenized foundation for a
dense, functional, single-operator admin tool — built to the constraints in the
product team's brief (`docs/DESIGN_SYSTEM_BRIEF.md`): buildless, offline-capable,
dark-mode-mandatory, and accessible (color is never the sole signal).

---

## Product context

- **Surface.** A single embedded admin panel (`src/ha_ops_mcp/static/ui.html`). It runs
  as a Home Assistant add-on and is viewed in an iframe inside the HA sidebar (also
  standalone). Single operator/admin — no multi-tenant, no consumer scale.
- **Stack.** One static HTML file. **Tailwind CSS 3.4.1 via CDN (no build step)** +
  **Alpine.js 3.13.5** for reactivity. Light / dark / auto theme already wired
  (`localStorage('haops-theme')`, `class="dark"` strategy on `<html>`).
- **Three tabs.** **Timeline** (the densest, most information-rich surface — it anchors
  the system), **Backups**, **Health**.
- **The forcing function.** A new *Timeline classification* feature gives every operation
  row an **op-class** pill (`READ` / `MUTATE` / `DELETE`) and an **area** tag
  (`·database·`, `·automation·`, …). The color language is now load-bearing for risk
  communication, so it had to be designed properly rather than borrowed from the four
  hand-rolled `sev-*` classes it started as.

### The job of this design system
Replace the four hardcoded `sev-safe / sev-warn / sev-breaking / sev-info` classes
(`ui.html` lines ~20–32) with a documented **two-tier token system** (primitive →
semantic), formalize the implicit Tailwind type/spacing scales, and spec the component
inventory — so a new tab can be built from tokens without inventing one-off hex values.

---

## Sources used to build this system

Everything here was reverse-engineered from the real product. If you have access, read
these to go deeper:

| Source | Where | What it gave us |
|---|---|---|
| Design brief | `docs/DESIGN_SYSTEM_BRIEF.md` | Constraints, deliverables, the `sev-*` seam to replace |
| The UI (source of truth) | `src/ha_ops_mcp/static/ui.html` (1,164 lines) | Every component, color, type size, interaction, the diff viewer |
| Op-class / area classification | `src/ha_ops_mcp/safety/classification.py` | The risk language + the 15 area names (authoritative) |
| Product README | `README.md` (repo root) | Voice, examples, capability framing |
| Implementation guide | `CLAUDE.md` (repo root) | Architecture, the "power tool / SSH to prod" framing |
| GitHub repo | https://github.com/dude84/ha-ops-mcp | The whole codebase — explore it to build higher-fidelity designs |
| Add-on icon | `logo.png` / `icon.png` (repo root → `assets/`) | The only brand mark (a generic robot-arm-with-wrench) |

> **Explore the repository** ([github.com/dude84/ha-ops-mcp](https://github.com/dude84/ha-ops-mcp))
> for richer context before building net-new surfaces — the tool descriptions, the
> Timeline endpoint, and `classification.py` are the best primary sources.

---

## Index — what's in this folder

| File / folder | Purpose |
|---|---|
| `README.md` | This file — context, content & visual foundations, iconography |
| `colors_and_type.css` | **The tokens.** Two-tier (primitive → semantic) colors + type/spacing/radii/shadow, light + dark |
| `IMPLEMENTATION.md` | One-page guide: wiring tokens into the buildless Tailwind `ui.html` + the `sev-*` → semantic migration table |
| `RELEASE_BRIEF_v1.0.md` | Versioned handoff brief for the dev implementation team — scope, steps, acceptance criteria, deferred decisions |
| `CHANGELOG.md` | Versioned log of every change — v1.0 (foundations) and v1.1 (brand mark) |
| `SKILL.md` | Agent-Skill manifest so this system works as a downloadable Claude skill |
| `assets/` | `logo.svg` + `logo-128/256/512.png` (v1.1 mark), `logo.png`/`icon.png` (live add-on icon), `favicon.svg` + `favicon-16/32/64.png`, `area-icons.svg` sprite and `area-icons/` (15+misc SVGs) |
| `preview/` | Small HTML specimen cards that populate the Design System tab |
| `ui_kits/sideload-ui/` | High-fidelity, interactive recreation of the admin panel (Timeline / Backups / Health) — `README.md`, `index.html`, and JSX components |

There are no `fonts/` — the system deliberately uses the platform font stack (see
*Visual foundations → Typography*) to honor the offline constraint.

---

## CONTENT FUNDAMENTALS

The voice is that of a **senior operator writing to another operator** — technically
precise, calm, and candid about risk. It never markets; it informs and warns.

**Tone & stance.** Direct and unhedged. The product README opens with a bold warning,
not a pitch: *"This is a power-user tool. It can break your Home Assistant as easily as
you can — possibly faster."* The governing metaphor is repeated everywhere: *"Treat this
like SSH access to production — because that's what it is."* The UI inherits that
seriousness — copy exists to help a reviewer understand consequences before they act.

**Person.** Addresses the operator as **"you"**; refers to the assistant/system in the
third person ("the assistant reads the dashboard, builds a JSON Patch, shows you the
diff"). Instructions to the user are imperative: *"Click a row to see the diff."*

**Casing.**
- UI labels & buttons: **Sentence case** — "Show reads", "Clear audit log", "Prune now
  (use retention)", "Clear all now", "What do the tags mean?".
- Op-class pills: **UPPERCASE**, always — `READ` / `MUTATE` / `DELETE`. (The label is the
  accessibility signal; uppercase makes it scan as a category, not prose.)
- State pills: **lowercase** — `ok` / `fail`. Tab section captions like "By type",
  "Last prune" are **Title-ish sentence case**; tiny stat captions are **UPPERCASE**
  ("TOTAL BACKUPS", "DISK USAGE").
- Tool names, paths, tokens: verbatim **monospace**, never re-cased
  (`haops_backup_revert`, `/config/.storage/lovelace`).

**Microcopy patterns.**
- *Explain the consequence, then the escape hatch.* Confirm dialogs spell out exactly
  what will change and that it's irreversible: *"This is irreversible. Backups restored
  via haops_backup_revert will no longer be available."*
- *Name the boundary honestly.* *"HA side effects fired during the original apply are
  NOT rolled back."* The system never over-promises safety.
- *Empty states teach.* Instead of a bare "No data", they explain the mechanism:
  *"No prune has been logged yet. Retention runs automatically after every backup
  write — this will populate once the first prune removes something."*
- *Defer to the real flow.* Admin-convenience actions point back to the canonical MCP
  tools: *"Full control (revert, targeted deletion) stays in the MCP flow — use
  `haops_backup_revert` / `haops_backup_prune`."*

**Numerals & units.** Plain and unfussy: byte sizes auto-scale (`B / KB / MB / GB`),
ages are in days, timestamps are localized (`Apr 21, 2026, 14:03:07`). Counts are
written out with the noun pluralized via `(s)` — "Prune 12 backup(s)".

**Emoji.** **Not used as content.** A small set of **Unicode glyph icons** stands in for
an icon font (offline constraint) — `☀ ☾ ◐` for the theme cycle, `▸ ▾` disclosure
triangles, `↺ ↶ ✓ ←  →` for relational/nav affordances. These are functional symbols,
not decorative emoji. See *Iconography*.

**The vibe:** an audit log you'd trust in production. Information-dense, monospace where
it counts, no chrome that doesn't earn its place, and unflinching about what can't be undone.

---

## VISUAL FOUNDATIONS

The aesthetic is **utilitarian admin density** — a calm, neutral canvas where *color is
reserved almost entirely for risk and state*. Think a well-built CLI dashboard rendered
in HTML: flat, legible, fast.

### Color
- **Neutral-dominant.** The interface is gray top to bottom — `gray-50` body / white
  cards in light, `gray-900` body / `gray-800` cards in dark. Color is spent
  deliberately, not decoratively.
- **One accent: blue.** `blue-600` (`#2563eb`) marks the single primary action and the
  active tab. That's it — no secondary brand hue, no gradients.
- **Color = risk + state, the load-bearing language.** This is the heart of the system:
  - `READ` → **gray** (`--op-read`) — observes, changes nothing.
  - `MUTATE` → **amber** (`--op-mutate`) — changes state, recoverable.
  - `DELETE` → **red** (`--op-destructive`) — irreversible / data loss.
  - `ok` → **green** (`--state-ok`), `fail` → **red** (`--state-fail`).
  - Relational links (paired rollback/apply rows) → **indigo**, the one extra hue,
    used only for cross-references.
- **Every token has a light and a dark value** (see `colors_and_type.css`). Dark isn't a
  filter — pill backgrounds flip to deep-900 fills with -200 text, meeting AA both ways.
- **Diff viewer** has its own syntax palette (green add / red remove / blue hunk / cyan
  YAML keys / purple numbers / orange booleans / emerald strings / yellow changed) —
  scoped to the `<pre>`, never leaking into chrome.
- **Imagery:** essentially none. This is a data tool — no photography, no illustration,
  no hero imagery. The only raster asset is the add-on icon.

### Typography
- **System font stack, by mandate** (offline): `ui-sans-serif, system-ui, -apple-system,
  "Segoe UI", Roboto, sans-serif`. No webfonts.
- **Monospace pulls real weight.** `ui-monospace, SFMono-Regular, Menlo, …` is used
  everywhere machine-shaped: timestamps, tool names, paths, tokens, `·area·` tags,
  key/value blocks, and the whole diff viewer. The sans/mono contrast *is* the visual
  rhythm of the product.
- **Scale** (Tailwind defaults, formalized): `xs .75 / sm .875 / base 1 / lg 1.125 /
  xl 1.25 / 2xl 1.5 rem`. Most of the UI lives at **xs and sm** — it's a dense tool.
  `2xl` is reserved for stat-card numbers, `xl` for tab headings, `lg` for the wordmark.
- **Weights:** 400 / 500 (medium — row titles, buttons, active nav) / 600 (semibold —
  headings, stats, wordmark). No light or black weights.
- **Uppercase micro-labels** (`text-xs`, `~0.05em` tracking) caption stats and groups.

### Spacing, radii, shadow, borders
- **Spacing** follows the Tailwind step scale: `0.5 / 1 / 2 / 3 / 4 / 6` (rem
  fractions). Header is `px-6 py-3`; page is `p-6` capped at `max-w-7xl` and centered;
  cards are `p-3`–`p-4`; rows are `px-4 py-3`. **Lay groups out with flex/grid + `gap`**,
  not margins.
- **Radii — two only:** `0.25rem` (`--radius-sm`) for badges, buttons, tabs, chips,
  inline code; `0.5rem` (`--radius-lg`) for cards, panels, legend, banners. Nothing is
  fully rounded; nothing is sharp-cornered.
- **Shadow — flat by design.** A single resting elevation: `shadow-sm`
  (`0 1px 2px rgba(0,0,0,.05)`) on cards. No layered shadows, no glow. **Dark mode uses
  no shadow at all** — surfaces separate by lightness (`gray-900` vs `gray-800`) instead.
- **Borders** are the primary separator: `1px` `gray-200` (light) / `gray-700` (dark) on
  cards and controls; lighter `gray-100` / `gray-700` for internal dividers between
  rows, table rows, and check entries.
- **No protection gradients, no blur, no glass.** Surfaces are opaque. The only
  transparency is in a couple of dark-mode semantic banners (e.g. `red-900/30`) and the
  pair-highlight ring.

### Backgrounds
Flat solid fills only — `--surface` (body) and `--surface-raised` (cards). No image
backgrounds, no repeating patterns, no textures, no gradients anywhere in the chrome.

### Cards
`bg-white` / `dark:bg-gray-800`, `rounded-lg`, `shadow-sm`, `p-3`–`p-4`. Stat cards add a
small uppercase caption over a `2xl` semibold number. Timeline rows are cards with a
full-width clickable header that expands to reveal a bordered detail region.

### Interaction, hover & press
- **Hover is subtle.** Ghost controls (tabs, nav, table actions) → `hover:bg-gray-100` /
  `dark:hover:bg-gray-700`. Solid buttons → one shade darker (`blue-600` → `blue-700`,
  `amber-600` → `amber-700`, `red-600` → `red-700`). Text links darken
  (`red-600` → `red-800`).
- **Active tab** = filled accent: `bg-blue-600 text-white`. Toggle chips (Show reads)
  fill accent when on, ghost-outline when off.
- **No press-shrink, no scale transforms.** Press feedback is the color-darken only.
- **Disabled** = `opacity-40`–`50` (ghost) or a lighter solid fill (`blue-400`,
  `amber-400`, `red-400`) + `cursor-not-allowed`.
- **Focus / attention:** the one animated moment in the app — jumping to a paired row
  adds a `ring-2 ring-indigo-500` that fades after 1.5s, with `scroll` behavior smooth.

### Motion
**Minimal and functional.** No entrance animations, no parallax, no bounce. Alpine
`x-cloak` prevents flash-of-unstyled. Transitions are limited to the pair-jump ring
highlight and the native smooth-scroll. Disclosure (`▸`/`▾`) is an instant toggle.

### Layout rules
- Full-width header bar (wordmark + tab nav left; version, last-refreshed, theme toggle
  right), `1px` bottom border.
- Single centered content column, `max-w-7xl`, `p-6`.
- Stat grids: `grid-cols-2 md:grid-cols-4`. Tables go full-width inside a card with a
  captioned header row.
- Timeline is a vertical stack of row-cards with `space-y-2`.

---

## ICONOGRAPHY

**There is no icon font and no SVG icon set in the product** — and that's a deliberate
consequence of the *offline / no-CDN* constraint. The UI conveys affordances with a tiny,
curated set of **Unicode glyphs** rendered in the text color:

| Glyph | Meaning | Where |
|---|---|---|
| `☀` / `☾` / `◐` | theme = light / dark / auto | header theme-cycle button |
| `▸` / `▾` | collapsed / expanded | Timeline row disclosure |
| `↺` | rolled back | paired-row chip |
| `↶` | reverts apply | paired-row chip |
| `✓` | active / shown | "✓ Reads shown" toggle |
| `←` / `→` | newer / older | Timeline pagination |
| `·area·` | subsystem tag | mono dots around the area name |

**Rules for this system:**
- **Prefer Unicode glyphs** for the handful of affordances above — they need no asset and
  work offline. Keep them in `--text` / `--text-muted`, never colored decoratively.
- **The 15 area tags now have an optional self-hosted SVG icon layer** (added per the
  brief's floated iteration — see *Area icon set* below). The text tag (`·config·`) remains
  the accessible baseline; the icon is **additive, never a replacement** for the label.
- **The 15 areas** (authoritative, from `classification.py`): `config, automation,
  script, scene, dashboard, entity, registry, database, system, addon, shell, helper,
  backup, service, references`.
- **The add-on mark** (`assets/logo.svg` + `logo-128/256/512.png`, also `logo.png` / `icon.png`)
  is the **v1.1 redraw**: a robot hand gripping a cone drill bit, in the original cyan /
  indigo / green palette with a navy outline. The favicon (`assets/favicon.svg` +
  `favicon-16/32/64.png`) is a flat two-tone cut for tiny sizes. The in-app header stays
  **text-only** ("HA Ops") — the mark is for the add-on store, favicon, and docs.
- **No emoji** as content anywhere.

### Area icon set
A self-hosted icon layer for the 15 subsystem areas, honoring the offline constraint
(no icon-font CDN at runtime).

- **Source: [Lucide](https://lucide.dev) (ISC license)** — a neutral, single-weight
  (2px) stroke set that matches the utilitarian admin aesthetic. **Flagged as a
  substitution:** the product ships no icons of its own, so this is the closest-matching
  open set rather than a bespoke design. Swap freely if you commission custom marks.
- **Delivery:** one sprite, `assets/area-icons.svg` — `<symbol id="area-{name}">` per
  area; reference with `<use href="…/area-icons.svg#area-config">` (or inline the sprite,
  as the UI kit does). Individual files also live in `assets/area-icons/{area}.svg`.
- **Mapping:** config→settings, automation→zap, script→scroll-text, scene→drama,
  dashboard→layout-dashboard, entity→box, registry→library, database→database,
  system→cpu, addon→puzzle, shell→terminal, helper→sliders-horizontal, backup→archive,
  service→play, references→network.
- **Usage:** render at 13–14px in `--text-muted` / `--text-faint`, immediately left of the
  `·area·` text. Never use the icon alone. See it in context in the UI kit's Timeline rows
  and on the *Area icons* spec card.

### Substitution flagged
The **area-icon set is a Lucide substitution** (the product has no native icons). The
glyph affordances (`▸ ☾ ↺`) are intrinsic Unicode, not substituted. If you commission a
bespoke icon set, keep a single consistent stroke weight and re-export `area-icons.svg`
with the same `area-{name}` symbol ids — everything downstream will pick it up.

---

*Built from the ha-ops-mcp codebase. See `colors_and_type.css` for the tokens,
`ui_kits/sideload-ui/` for the component recreations, and the
[GitHub repo](https://github.com/dude84/ha-ops-mcp) for the source of truth.*
