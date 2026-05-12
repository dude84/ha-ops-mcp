# Approved backlog

Non-critical items that have been discussed, scoped, and approved for
implementation — just not scheduled yet. New items land here after they
survive a triage pass; truly speculative ideas stay in `_gaps/`.

When you pick one up: move it into a change plan, implement, then delete
the entry (not strike-through) on the merge commit.

---

## Prune stale public releases that did not fix the OAuth-expiry regression

The 0.33.3 → 0.33.7 sequence iterated several times on the
`access_token_ttl` default and the OAuth-store clear UX before landing
the right shape in 0.33.8 (`clear_oauth_on_next_boot` self-resetting
checkbox). The middle releases each technically built and shipped, but
none of them is what a user updating from 0.33.2 should land on — every
intermediate version either keeps the wrong default TTL, the wrong
reset UX (`auth_reset_marker` string), or still ships the self-DoS
`haops_auth_clear` tool. Leaving them visible on the GitHub Releases
page is a footgun for anyone reading the version history backwards.

When picking this up: delete (or convert to draft) the GitHub releases
that didn't fix the issue end-to-end, keeping only the ones that
represent a coherent end-state.

- **Keep:** 0.33.2 (last good before the iteration) and 0.33.8 (the
  actual fix).
- **Remove from public Releases page:** 0.33.3, 0.33.4, 0.33.5, 0.33.6,
  0.33.7.
- **Git tags themselves stay** — they're history — but the public
  Releases page should not advertise the intermediate steps.

Command shape: `gh release delete vX.Y.Z --cleanup-tag=false`
(preserves the tag, drops the Release entry). Confirm with the user
before each delete in case any of them have been linked from external
places.

