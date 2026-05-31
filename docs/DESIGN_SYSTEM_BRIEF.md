# ha-ops-mcp Sideload UI — Design System Brief

> For the design team. Goal: turn the ad-hoc CSS in the sideload UI into a real,
> documented design system that scales as the panel grows beyond its current three tabs.

## TL;DR

The sideload admin UI (`src/ha_ops_mcp/static/ui.html`) styles everything with Tailwind
utility classes plus four hand-rolled severity classes (`sev-safe/warn/breaking/info`,
hardcoded hex, lines 20–32). That worked for v1 but is now the ceiling. We want a
two-tier token system (primitive → semantic), a documented component inventory, and a
formal theming spec — delivered in a way that respects the buildless reality of this
project.

## Why now

We just shipped a **Timeline classification** feature: every operation row now carries an
**op-class** pill (`READ` / `MUTATE` / `DELETE`) and an **area** tag (`·database·`,
`·automation·`, …). To build it we reused the existing `sev-*` classes as a stopgap:

| op-class | meaning | current color |
|----------|---------|---------------|
| `read` | observes state, changes nothing | `sev-info` (gray) |
| `mutate` | changes state, recoverable | `sev-warn` (amber) |
| `destructive` | irreversible / data loss | `sev-breaking` (red) |

That feature is the forcing function: the color language is now load-bearing for risk
communication, so it needs to be designed properly, not borrowed from a 4-class hack.

## Product context

- **Surface**: an embedded admin panel for `ha-ops-mcp`, an MCP server for Home
  Assistant power users. Runs as an HA addon, viewed in an iframe inside the HA sidebar
  and also standalone. Single operator/admin — no multi-tenant, no consumer scale.
- **Current stack**: a single `static/ui.html` file. **Tailwind CSS via CDN (no build
  step)**, **Alpine.js** for reactivity, light/dark/auto theme already wired
  (`localStorage('haops-theme')`, `class="dark"` strategy).
- **Tabs today**: Timeline, Backups, Health. **Timeline is the densest, most
  information-rich surface and should anchor the system.**

## Hard constraints (read before proposing anything)

1. **Buildless is the current reality.** Tailwind loads from a CDN; there is no bundler.
   The DS must either (a) stay buildless — CSS custom properties consumed by an inline
   `tailwind.config` — or (b) explicitly justify introducing a minimal build step. Flag
   this as decision #1; don't silently assume a build pipeline.
2. **Offline / local.** Runs on a home server, sometimes no internet. No reliance on
   external font/icon CDNs at runtime (self-host if needed).
3. **Dark mode is mandatory**, not an afterthought — many operators run HA dark. Every
   token needs a light + dark value.
4. **Accessibility**: color is never the sole signal. The op-class pills carry text
   labels (`READ`/`MUTATE`/`DELETE`) precisely so colorblind users aren't reliant on hue
   — keep that principle. Target WCAG AA contrast for text on every surface.
5. **No heavy framework migration** as part of this. Alpine + utility CSS stays unless a
   migration is separately scoped and approved.

## Deliverables requested

1. **Token set** (two-tier: primitive → semantic):
   - Primitive ramps (neutral, blue, amber, red, green) with light + dark values.
   - Semantic tokens: `surface`, `surface-raised`, `border`, `text`, `text-muted`,
     `accent`, plus **op-class / state** tokens: `--op-read`, `--op-mutate`,
     `--op-destructive`, `--state-ok`, `--state-fail`. These must cover every current use
     of `sev-safe/warn/breaking/info` so they can be swapped in directly.
   - Delivered as CSS custom properties + a mapping table the inline Tailwind config can
     read.
2. **Area visual language**: a defined treatment for the area tags. The full area set is:
   `config, automation, script, scene, dashboard, entity, registry, database, system,
   addon, shell, helper, backup, service, references`. We chose *text tags* (`·area·`)
   for v1 — define their typography/color, and you *may* propose an optional icon layer
   for a future iteration (icons must be self-hosted SVG, no icon-font CDN).
3. **Component inventory & specs** for what exists: app shell/header, tab nav,
   badge/pill, filter chip, timeline row (collapsed + expanded), diff viewer, key/value
   detail block, button (primary / secondary / danger), empty state, loading state,
   toast/inline result.
4. **Typography & spacing scales** — currently implicit Tailwind defaults; formalize.
5. **Theming spec**: how light/dark/auto map onto the semantic tokens. The toggle UX
   already exists (cycles auto → light → dark).
6. **A reference artifact** (Figma library or equivalent) + a one-page implementation
   guide for wiring tokens into the buildless Tailwind setup.

## Non-goals

- Not redesigning HA itself or matching HA's internal theme tokens. (HA has its own
  theming; matching it is explicitly out of scope for v1.)
- No new framework, no SPA router, no component-library dependency.
- No marketing / brand identity work — this is a functional admin tool.

## Success criteria

- The four `sev-*` hardcoded classes are gone, replaced by semantic tokens.
- A new tab or component can be built from documented tokens + specs without inventing
  one-off hex values.
- The Timeline op-class pills and area tags are expressed purely in DS tokens.

## Reference: where things live

| Thing | Location |
|-------|----------|
| The UI | `src/ha_ops_mcp/static/ui.html` (single file) |
| Current severity classes (the seam to replace) | `ui.html` lines ~20–32 |
| Timeline row markup | `ui.html` Timeline `<section>` |
| Op-class / area source of truth (backend) | `src/ha_ops_mcp/safety/classification.py` |
