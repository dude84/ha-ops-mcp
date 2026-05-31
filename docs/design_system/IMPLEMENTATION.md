# HA Ops Design System — Implementation Guide (v1.0)

One page: how to wire the tokens into the **buildless** `static/ui.html` (Tailwind CDN +
Alpine) and retire the four hardcoded `sev-*` classes. No build step required.

---

## Decision #1 — stay buildless ✅

The brief asks us to flag the build question first. **Recommendation: stay buildless.**
Everything here is plain CSS custom properties + an inline `tailwind.config` that reads
them. No bundler, no PostCSS, no migration. If a build step is ever introduced for other
reasons, these same tokens port directly into a `tailwind.config.js` `theme.extend`.

---

## Step 1 — load the tokens

Self-host `colors_and_type.css` next to `ui.html` and link it (offline-safe, no CDN):

```html
<link rel="stylesheet" href="./colors_and_type.css">
```

It defines Tier-1 primitives and Tier-2 semantic tokens, with light values on `:root` and
dark values under `.dark` — matching the existing `class="dark"` strategy already wired to
`localStorage('haops-theme')`. Nothing about the theme toggle changes.

---

## Step 2 — map tokens into the inline `tailwind.config`

Replace the current inline config (`{ darkMode: 'class' }`) with the block below. It
exposes the semantic tokens as Tailwind color utilities that resolve through the CSS vars,
so **both themes are handled by the vars** — you write `bg-surface` once, not
`bg-white dark:bg-gray-800`.

```html
<script>
  // ... keep the existing pre-paint dark-mode IIFE above ...
  window.tailwind = window.tailwind || {};
  window.tailwind.config = {
    darkMode: 'class',
    theme: {
      extend: {
        colors: {
          surface:        'var(--surface)',
          'surface-raised':'var(--surface-raised)',
          'surface-sunken':'var(--surface-sunken)',
          'surface-hover':'var(--surface-hover)',
          border:         'var(--border)',
          'border-subtle':'var(--border-subtle)',
          text:           'var(--text)',
          'text-muted':   'var(--text-muted)',
          'text-faint':   'var(--text-faint)',
          accent:         { DEFAULT: 'var(--accent)', hover: 'var(--accent-hover)', soft: 'var(--accent-soft)' },
          // op-class (risk language)
          'op-read-bg':   'var(--op-read-bg)',   'op-read-fg':   'var(--op-read-fg)',
          'op-mutate-bg': 'var(--op-mutate-bg)', 'op-mutate-fg': 'var(--op-mutate-fg)',
          'op-dstr-bg':   'var(--op-destructive-bg)', 'op-dstr-fg': 'var(--op-destructive-fg)',
          // state
          'state-ok-bg':  'var(--state-ok-bg)',  'state-ok-fg':  'var(--state-ok-fg)',
          'state-fail-bg':'var(--state-fail-bg)','state-fail-fg':'var(--state-fail-fg)',
        },
        borderRadius: { sm: 'var(--radius-sm)', lg: 'var(--radius-lg)' },
        boxShadow:    { sm: 'var(--shadow-sm)' },
      }
    }
  };
</script>
```

Now utilities like `bg-surface`, `text-text-muted`, `bg-op-mutate-bg text-op-mutate-fg`,
`border-border`, `rounded-lg`, `shadow-sm` all resolve to the right value in **both**
themes automatically.

---

## Step 3 — delete the `sev-*` block, migrate every use

Remove lines ~20–32 of `ui.html` (the four `sev-*` classes and their `.dark` overrides).
Replace the `.badge` helper with a token-driven version and migrate each call site:

```css
/* old:  .sev-safe / .sev-warn / .sev-breaking / .sev-info  → DELETE */
.badge { @apply inline-block px-2 py-0.5 rounded text-xs font-medium; }
```

| Old class | Meaning | New utilities | Token |
|---|---|---|---|
| `sev-info` | op-class **read** | `bg-op-read-bg text-op-read-fg` | `--op-read-*` |
| `sev-warn` | op-class **mutate** | `bg-op-mutate-bg text-op-mutate-fg` | `--op-mutate-*` |
| `sev-breaking` | op-class **destructive** / fail | `bg-op-dstr-bg text-op-dstr-fg` | `--op-destructive-*` |
| `sev-safe` | state **ok** | `bg-state-ok-bg text-state-ok-fg` | `--state-ok-*` |

The Alpine helpers `opBadgeClass()` / `opLabel()` map cleanly:

```js
// before:  c === 'read' ? 'sev-info' : c === 'destructive' ? 'sev-breaking' : 'sev-warn'
opBadgeClass(c) {
  return c === 'read'        ? 'bg-op-read-bg text-op-read-fg'
       : c === 'destructive' ? 'bg-op-dstr-bg text-op-dstr-fg'
       :                       'bg-op-mutate-bg text-op-mutate-fg';
}
// success/fail pill:
//   :class="e.success ? 'bg-state-ok-bg text-state-ok-fg' : 'bg-state-fail-bg text-state-fail-fg'"
```

Diff-viewer colors map to the `--diff-*` tokens the same way (replace the hardcoded
`text-green-700 dark:text-green-400` etc. with single classes backed by `--diff-add`,
`--diff-remove`, `--diff-hunk`, … if you also add them to the `colors` map above).

---

## Step 4 — area icons (optional layer)

Self-host `assets/area-icons.svg` and render the icon left of the existing `·area·` text
(never instead of it):

```html
<span class="inline-flex items-center gap-1 font-mono text-xs text-text-faint">
  <svg class="w-3.5 h-3.5"><use href="./assets/area-icons.svg#area-' + e.area + '"></use></svg>
  ·<span x-text="e.area"></span>·
</span>
```

---

## Success criteria (from the brief) — how this meets them

- ✅ **The four `sev-*` classes are gone**, replaced by semantic tokens (Step 3).
- ✅ **A new tab/component builds from tokens** — `bg-surface`, `text-text-muted`,
  `rounded-lg`, op-class/state utilities — **no one-off hex**.
- ✅ **Timeline op-class pills + area tags are pure DS tokens** (Steps 2–4).
- ✅ **Buildless preserved**, dark mode handled by the vars (no `dark:` pairs needed for
  semantic colors), offline-safe (self-hosted CSS + SVG, no runtime CDN for tokens/icons).

See `README.md` for the full foundations and `ui_kits/sideload-ui/` for a working
reference that already consumes these tokens.
