# HA Ops Design System — v1.0 Release Brief

**For:** the ha-ops-mcp implementation team
**Status:** Released · **Version:** 1.0 · **Date:** 2026-05-31
**Scope:** the sideload admin UI (`src/ha_ops_mcp/static/ui.html`)
**Source brief:** `docs/DESIGN_SYSTEM_BRIEF.md`

---

## 1. What v1.0 delivers

A documented, two-tier (primitive → semantic) token system that replaces the four
hand-rolled `sev-*` classes, formalizes the type/spacing scales, specs the component
inventory, and adds an optional self-hosted icon layer for the 15 subsystem areas — all
**buildless and offline-safe**, with full light + dark parity.

| Deliverable (from the brief) | Status | Where |
|---|---|---|
| Two-tier token set (primitive → semantic), light + dark | ✅ | `colors_and_type.css` |
| op-class / state tokens covering all `sev-*` uses | ✅ | `--op-*`, `--state-*` |
| Area visual language (text tag + optional icon layer) | ✅ | `assets/area-icons.svg`, README → Iconography |
| Component inventory & specs | ✅ | `preview/*` (21 cards), `ui_kits/sideload-ui/` |
| Typography & spacing scales | ✅ | `colors_and_type.css`, type/spacing cards |
| Theming spec (light/dark/auto) | ✅ | README → Visual Foundations; tokens under `:root` / `.dark` |
| Reference artifact + 1-page impl guide | ✅ | `ui_kits/sideload-ui/`, `IMPLEMENTATION.md` |

---

## 2. What the dev team should do (in order)

1. **Read `IMPLEMENTATION.md`** — it is the build instructions. Four steps, no bundler.
2. **Self-host `colors_and_type.css`** beside `ui.html` and `<link>` it.
3. **Swap the inline `tailwind.config`** for the mapping block in the guide (exposes
   `bg-surface`, `bg-op-mutate-bg`, `text-text-muted`, etc., resolving through the CSS
   vars so both themes work without `dark:` pairs).
4. **Delete the `sev-*` block** (`ui.html` lines ~20–32) and migrate every call site using
   the migration table. Update `opBadgeClass()` per the guide.
5. **(Optional) Add the area-icon layer** — self-host `assets/area-icons.svg`, render the
   `<use>` glyph left of the existing `·area·` text. Never replace the text label.
6. **Verify** against the success criteria below, in both light and dark.

---

## 3. Acceptance criteria (Definition of Done for v1.0)

- [ ] The four `sev-*` classes and their `.dark` overrides are removed from `ui.html`.
- [ ] Every former `sev-*` use renders via semantic tokens (op-class + state).
- [ ] Timeline op-class pills (`READ`/`MUTATE`/`DELETE`) and area tags use only DS tokens.
- [ ] A new component can be built with zero one-off hex values.
- [ ] Light **and** dark both pass a visual check; every pill meets WCAG AA.
- [ ] op-class pills still carry their text label (color is never the sole signal).
- [ ] No new runtime CDN dependency for tokens or icons (offline constraint holds).

---

## 4. Hard constraints honored (do not regress)

1. **Buildless** — CSS custom properties + inline `tailwind.config`. No bundler introduced.
2. **Offline** — system font stack, self-hosted CSS + SVG. No font/icon CDN at runtime.
3. **Dark mandatory** — every semantic token has a light + dark value; `class="dark"` strategy unchanged.
4. **Accessibility** — text labels on pills retained; AA contrast targeted on every surface.
5. **No framework migration** — Alpine + utility CSS stays. (The React UI kit is a *reference recreation*, not the shippable code.)

---

## 5. Open decisions deferred to v1.1 (non-blocking)

These shipped with a sensible default in 1.0; flag if you want them changed:

- **Area icons are a Lucide substitution** (ISC license), not bespoke marks — the product
  has no native icons. Sprite structure is stable: re-export `area-icons.svg` with the same
  `area-{name}` symbol ids to swap in custom art with zero downstream changes.
- **Two icon mappings are judgment calls:** `scene → drama` (theater masks) and
  `service → play`. Swap candidates noted in README if they read wrong.
- **`misc` catch-all icon** (dashed `shapes`) covers the `("mutate", "misc")` fallback for
  unrecognized tools — confirm the glyph choice.
- **Kit theme default** is auto-follow-system; change if a fixed default is preferred.

---

## 6. File manifest (v1.0)

| Path | Role |
|---|---|
| `README.md` | Foundations — context, content & visual rules, iconography, index |
| `IMPLEMENTATION.md` | **Build instructions** — buildless wiring + `sev-*` migration table |
| `colors_and_type.css` | The tokens (primitive → semantic; type/spacing/radii/shadow) |
| `assets/area-icons.svg` | 16-symbol sprite (15 areas + `misc`); per-area files in `assets/area-icons/` |
| `assets/logo.png`, `icon.png` | The add-on store mark |
| `preview/*.html` | 21 spec cards (Colors / Type / Spacing / Components / Brand) |
| `ui_kits/sideload-ui/` | Interactive reference recreation + reusable JSX components |
| `SKILL.md` | Downloadable Claude-skill manifest |

---

*Questions on intent → `README.md`. Questions on wiring → `IMPLEMENTATION.md`.
Source of truth for the product → [github.com/dude84/ha-ops-mcp](https://github.com/dude84/ha-ops-mcp).*
