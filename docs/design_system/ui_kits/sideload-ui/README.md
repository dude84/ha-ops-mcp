# Sideload UI — UI Kit

A high-fidelity, interactive recreation of the **ha-ops-mcp sideload admin panel** — the
embedded HA sidebar UI defined in `src/ha_ops_mcp/static/ui.html`. Built from the real
source (not screenshots): same layout, same copy, same interactions, same color language.

It is a **recreation, not production code** — interactivity is faked (no backend, no
fetch), components are cosmetic, and the data is a static fixture. The point is
pixel-fidelity and reusable, well-factored components you can lift into new mocks.

## Run it
Open `index.html`. React + Babel load from CDN; tokens come from the root
`colors_and_type.css`, layout from `ui.css`. The kit styles everything with **semantic
classes mapped to the design tokens** — the original UI uses Tailwind utilities, but this
kit proves the token set covers the real surface without them.

## What's interactive
- **Tab nav** — Timeline / Backups / Health (persists to `localStorage`).
- **Theme cycle** — auto → light → dark (`◐ / ☀ / ☾`), shares `haops-theme` key with the real UI.
- **Timeline** — click any row to expand its diff / details / backup / token; **Show reads**
  toggle filters read-only ops in/out; **legend** toggle; **paired-row jump** (↺ / ↶) scrolls
  to and flashes the linked apply/rollback row; **Revert** runs a confirm flow.
- **Backups** — **Prune now** runs a confirm → result-banner flow; per-type table; stat cards.

## Components
| File | Component | Notes |
|---|---|---|
| `helpers.jsx` | `Badge`, `DiffView`, `fmtTs`, `fmtBytes`, `opLabel/opClass` | Primitives + the diff line-colourer (ported from `renderDiffHtml`) |
| `Header.jsx` | `Header` | Wordmark + tab nav + version + last-refreshed + theme toggle |
| `TimelineTab.jsx` | `TimelineTab`, `TimelineRow` | The anchor surface — op-class pills, area tags, expandable diffs, pairing |
| `BackupsTab.jsx` | `BackupsTab` | Stat-card grid, per-type table, last-prune block, prune/clear actions |
| `HealthTab.jsx` | `HealthTab` | self_check entries + tools_check groups with per-test status |
| `app.jsx` | `App` | Tab + theme state; mounts the header and active tab |
| `data.js` | `window.HAOPS_DATA` | Static fixture — timeline entries, backups summary, health probes |

## Fidelity notes / intentional gaps
- The real UI is buildless **Tailwind + Alpine.js**; this kit is **React + token CSS**. Visual
  output matches; implementation deliberately differs (cosmetic recreation).
- Pagination, auto-refresh polling, and lazy diff-loading are represented visually but not
  wired (single fixture page).
- There is **no logo in the header** — the real UI is text-only ("HA Ops"). Matched here.
- Backend calls (`/api/ui/*`) are stubbed with `setTimeout` + confirm dialogs.

Source of truth: [`dude84/ha-ops-mcp`](https://github.com/dude84/ha-ops-mcp) →
`src/ha_ops_mcp/static/ui.html` and `src/ha_ops_mcp/safety/classification.py`.
