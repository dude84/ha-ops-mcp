# UI Performance Baseline — `new-dashboard`

Standing perf/freeze baseline for the primary dashboard, captured with the
addon's headless Playwright tools (`haops_ui_perf`, `haops_ui_interact`). Re-run
the same sweep after dashboard changes and diff against this table to catch
regressions. Numbers are headless-Chromium on the HA host — treat as **relative**
signal (rank views against each other), not absolute companion-app timings.

Metric legend: **LT** = main-thread long-tasks (the freeze signal — blocked main
thread); **CLS** = cumulative layout shift (visual jank on load); **DOM** = dom
nodes; **heap** = JS heap MB; **cards** = rendered `ha_cards` (incl. nested).

---

## Run 1 — PASSIVE — 2026-06-08

**Method:** load each view (`ui_perf`, settle 2500 ms) at desktop **1280×800**
and mobile **402×874 @3×** (the companion-app surface); then scroll-sweep
(`ui_interact`, scroll/wait actions only — **zero control taps**, fully passive)
the heaviest views at mobile. Dashboard `new-dashboard`, 14 non-empty views.
HA 2026.6.1, addon v0.53.3.

### Phase A — load metrics

| View | VP | nav ms | LCP | CLS | LT cnt/total/max ms | DOM | heap | cards | err |
|---|---|---|---|---|---|---|---|---|---|
| Home | D | 2624 | 128 | **0.82** | 13 / **1271** / 276 | 1381 | 65 | 21 | 0 |
| Home | M | 2546 | 52 | 0.65 | 5 / 372 / 120 | 1563 | 54 | 21 | 0 |
| Baby | D | 2597 | 1676 | 0.27 | 3 / 412 / 212 | 4316 | 65 | 26 | 0 |
| Baby | M | 2536 | 1500 | 0.55 | 4 / 403 / 193 | 4256 | 58 | 26 | 0 |
| Bedroom | D | 2576 | 2080 | 0.31 | 6 / 776 / 274 | 7008 | 61 | 27 | 0 |
| Bedroom | M | 2555 | 1844 | 0.26 | 6 / 809 / 299 | **7083** | 61 | 27 | **2** |
| Living Room | D | 2537 | 1508 | 0.29 | 7 / **1300** / 365 | **10043** | 54 | 33 | **2** |
| Living Room | M | 2543 | 1444 | 0.61 | 4 / 301 / 124 | 1685 | 58 | 33 | **2** |
| Office | D | 2546 | 48 | 0.60 | 5 / 346 / 92 | 1843 | 54 | 51 | 0 |
| Office | M | 2542 | 64 | 0.00 | 5 / 492 / 194 | 1830 | 58 | 51 | 0 |
| Kitchen | D | 2532 | 44 | 0.25 | 4 / 369 / 153 | 1839 | 58 | 38 | 0 |
| Kitchen | M | 2531 | 40 | 0.01 | 4 / 392 / 178 | 1786 | **82** | 38 | 0 |
| Walkin | D | 2531 | 1412 | 0.27 | 8 / 932 / 305 | 7209 | 73 | 27 | 0 |
| Walkin | M | 2534 | 1784 | 0.47 | 5 / 721 / 259 | 6991 | 69 | 27 | 0 |
| Upstairs | D | 2531 | 1840 | 0.26 | 6 / 585 / 220 | 5350 | 61 | **133** | 0 |
| Upstairs | M | 2552 | 2036 | 0.48 | 6 / 619 / 262 | 5343 | 65 | **133** | 0 |
| Foie | D | 2545 | **2240** | 0.14 | 5 / 602 / 227 | 6564 | 54 | 14 | 0 |
| Foie | M | 2536 | 1676 | 0.35 | 5 / 571 / 204 | 6507 | 61 | 14 | 0 |
| AC Temp Mode | D | 2536 | 48 | 0.17 | 1 / 109 / 109 | 1410 | 51 | 50 | 0 |
| AC Temp Mode | M | 2537 | 36 | 0.33 | 4 / 294 / 119 | 1353 | 54 | 50 | 0 |
| Scheduler | D | 2532 | 52 | 0.01 | 2 / 166 / 115 | 739 | 51 | 2 | 0 |
| Weather | D | 2543 | 1100 | 0.07 | 3 / 263 / 105 | 988 | 54 | 10 | 0 |
| Weather | M | 2552 | 1068 | 0.01 | 3 / 274 / 119 | 931 | 54 | 10 | 0 |
| Admin | D | 2542 | 1136 | 0.16 | 2 / 179 / 126 | 1186 | 54 | 33 | 0 |
| Admin | M | 2542 | 984 | 0.56 | 1 / 96 / 96 | 1125 | 45 | 33 | 0 |
| Zigbee | D | 2550 | 52 | 0.00 | 2 / 169 / 114 | 845 | 48 | 2 | 0 |

(Scheduler/Zigbee mobile skipped — 1–2 card trivial views. `ha-ops-lab` empty, skipped.)

### Phase B-cam — passive camera dwell (mobile, 30 s settle)

Load the live-camera view and dwell 30 s, then read metrics. Tests whether live
streams leak memory or generate sustained main-thread jank.

| View | streams | LT @load → @30s | heap @load → @30s | DOM @load → @30s |
|---|---|---|---|---|
| Living Room | 3 | 4 → **18 (2043 ms)** | 58 → 57 (flat) | 1685 → **9821** |
| Bedroom | 2 | 6 → 6 (flat) | 61 → 54 (flat) | 7083 → 6973 (flat) |

**Finding:** no memory leak (heap flat). Living's **3 simultaneous WebRTC streams**
generate sustained recurring main-thread jank (~2 s blocked per 30 s, continuous)
+ progressive DOM growth; Bedroom's 2-stream view is quiet after load. Jank scales
with simultaneous live streams (3 = problem, 2 = fine). All camera cards are
`view.default: live`. **Lever: snapshot-default + cap simultaneous live streams
per view** (Living most affected).

### Phase B — scroll-sweep (mobile, scroll-only)

| View | scroll steps | LT during scroll (cnt/total ms) |
|---|---|---|
| Bedroom | 6 | **0 / 0** |
| Walkin | 6 | **0 / 0** |
| Foie | 6 | **0 / 0** |
| Home | 5 | **0 / 0** |
| Upstairs | 8 | **0 / 0** |

---

## Conclusions

1. **The freeze is NOT scroll-induced.** Every heavy view — including Bedroom/
   Walkin/Foie (~7k DOM) and Upstairs (133 cards) — scrolls with **0 long-tasks**.
2. **All main-thread cost is at LOAD / tab-switch.** Worst load blocks: Living
   1300 ms, Home 1271 ms (ApexCharts), Walkin 932 ms, Bedroom ~800 ms.
3. **Freeze hypothesis:** companion-app freeze = **rapid tab-switching between
   heavy views**, each costing 0.6–1.3 s of blocked main thread to render,
   compounded by **heap growth across views** (50→82 MB). Mobile webview is more
   constrained than headless desktop Chromium, so the same work bites harder.
4. **CLS epidemic** — Home 0.82, many views >0.25 — ApexCharts reflow on load.
   Visual jank + extra layout work; not a freeze cause but a quality issue.
5. **DOM-bomb cards:** Foie = 14 cards but 6.5k DOM (history-graph/BBQ-filter
   cards); Living 10k DOM (desktop). History-graph + nested-card explosion
   (Upstairs 133, Office 51) are the DOM drivers.

## Known issue surfaced

- **2× 404 console errors on Bedroom + Living** — the only two views with
  `custom:advanced-camera-card`. All 5 cameras are online (`recording`), so it is
  **not** a dead-snapshot 404. Benign console noise — cameras work. Root cause
  **unidentified**: the card auto-detects its engine from the entity platform
  (reolink/amcrest/generic here — none are Frigate, and there is no YAML key to
  override engine), so it is likely a snapshot/poster fetch from the Reolink/
  Generic engine, not a Frigate probe. Pinning the exact URL needs a CDP network
  trace (`ui_trace`). **Decision 2026-06-08: dropped** — cosmetic, not worth it.

## Fix directions (feed Task 3 — design-system rebuild)

- Cap ApexCharts / heavy cards per view; reduce ApexCharts CLS (reserve height).
- Trim history-graph DOM on Living / Bedroom / Walkin / Foie.
- Reduce nested-card explosion (Upstairs 133, Office 51 rendered cards).
- Investigate cross-view heap retention (50→82 MB) if freeze persists.

## Optimization 1 — CO2 charts → statistics (2026-06-11)

Converted the 5 CO2 ApexCharts (Home 4-series + Bedroom/Living/Office/Walk-in
1-series each) from raw history (`group_by avg 30min`, `curve smooth`, y `0–2000`)
to **`statistics: {type: max, period: 5minute}` + `curve straight` + y `400–2000`**.
Rationale: apexcharts-card fetches raw history per series then buckets in JS;
statistics hits the pre-aggregated table instead. `max` + `straight` + raised floor
also restore spike visibility (the `smooth`/avg/`0–2000` combo flattened peaks);
ceiling kept at 2000 because real 24h CO2 maxima hit ~2023 (Walk-in) → 1600 clips.

**Before → after (desktop long-tasks ms / CLS):**

| View | before | after | verdict |
|---|---|---|---|
| **Home** (3 apex charts) | **1271 / 0.82** | **347 / 0.27** | 🟢 **−73% LT, −67% CLS** (reproduced ~347 twice) |
| Living Room | 1300 / 0.29 | 1223 / 0.40 | flat — camera/DOM-bound |
| Office | 346 / 0.60 | 364 / 0.67 | flat — 1 chart ≠ bottleneck |
| Walk-in | 932 | 690 | within camera jitter |
| Bedroom | 776 | 1310 | camera jitter (↑) |

**Findings:**
- **Home was data/fetch-bound, not render-bound** — statistics conversion cut it
  ~3.6×. This is the real, reproducible win; Home was the dashboard's worst view.
- **Single-series room CO2 charts: no measurable client win** — those views are
  camera/DOM-bound; the one chart was never their bottleneck. (Server-side fetch +
  spike-UX still improved everywhere.)
- **Camera views swing ±400 ms run-to-run** (live WebRTC stream-startup timing is
  non-deterministic). Control: no-camera/no-CO2 views (Kitchen/AC/Weather/Admin)
  are flat ±30 ms = stable measurement. So camera-view deltas are jitter, not signal.
- **Recipe for reuse (Task 3):** statistics `max`/`5minute` + `straight` + tight-but-
  unclipped y-axis. Check real maxima before setting the ceiling.

## Not yet done

- **Run 2 (ACTIVE)** — tab-switch storm + open more-info dialogs to *reproduce*
  the freeze under the hypothesised trigger. Requires explicit approval (taps =
  not passive). This baseline is observation-only.
- **Temp charts** — same statistics recipe (`mean`, no clip concern) not yet applied.
