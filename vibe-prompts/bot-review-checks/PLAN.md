# Plan: surface "PR Reviewer" checks in the bot review pill

## Background

The bot reviewer runs as one or more GitHub **checks** whose names start with
`PR Reviewer` (these arrive as `CheckRun` nodes in `statusCheckRollup`). Today
these fall through `_bucket_for()` into the `"other"` bucket and are counted in
the **Main** checks pill. That's wrong on two counts:

1. A running reviewer check makes the Main pill go `pending` — the reviewer's
   progress is conflated with build/test status.
2. When the reviewer check finishes, its outcome disappears into the Main
   pass/fail count instead of being shown next to the other bot indicators.

We want the reviewer check to drive the **bot review pill** area instead:

- **running** → a pending bot pill (`🤖 ⏳`), so you can see the reviewer is
  working before it posts a review.
- **failed** (the check itself errored/timed out — the reviewer infra broke)
  → a **distinct** UI, visually separate from a bot leaving
  `CHANGES_REQUESTED`.
- and in all cases, **removed** from the Main checks pill.

Note there are two independent signals for the same bot and they must not be
confused:

- The bot **review** (GraphQL `reviews`, state `APPROVED` /
  `CHANGES_REQUESTED` / `COMMENTED`) — already handled by `bot_reviews()` /
  `render_bot_pill()` (`pr_core.py:523-543`). A `CHANGES_REQUESTED` here is a
  deliberate verdict and renders red with the `changes` class.
- The bot **reviewer check** (`CheckRun` named `PR Reviewer …`) — currently
  mis-bucketed into Main. This is what we're moving. A *failed* check means the
  reviewer didn't run to completion, which is NOT a verdict and must look
  different from `changes`.

## Changes

### 1. Add a constant to identify reviewer checks

In the constants block near the top (`pr_core.py:16-17`, alongside
`REQUIRE_REVIEW_CHECKS` and `E2E_SUBSTRINGS`):

```python
PR_REVIEWER_PREFIX = "PR Reviewer"
```

Add a small predicate next to `_bucket_for()`:

```python
def _is_reviewer_check(name):
    return name.startswith(PR_REVIEWER_PREFIX)
```

### 2. Filter reviewer checks out of the Main bucket

In `bucket_checks()` (`pr_core.py:191-226`), inside the loop over
`_dedupe_contexts(...)`, skip reviewer checks the same way
`REQUIRE_REVIEW_CHECKS` is skipped (`pr_core.py:201-203`):

```python
if _is_reviewer_check(name):
    continue
```

This keeps them out of both the `other` (Main) and `e2e` buckets. Dedup and
normalization stay unchanged, so a cancelled/retried reviewer run is handled by
the existing `_dedupe_contexts` logic.

### 3. Compute a reviewer-check status

Add a function that walks the rollup, picks out the reviewer checks, dedupes
them (reuse `_dedupe_contexts`), normalizes each with the existing
`_normalize_check_state()`, and collapses to a single status. It returns
`None` when there are no reviewer checks at all (so nothing renders).

```python
def reviewer_check_state(pr):
    """Collapsed state of the 'PR Reviewer' checks, or None if there are none.

    Returns one of: "pending", "errored", "success", "neutral".
    """
    rollup = pr.get("statusCheckRollup")
    if not rollup:
        return None
    states = []
    for node in _dedupe_contexts(rollup["contexts"]["nodes"]):
        if not _is_reviewer_check(_check_name(node)):
            continue
        s = _normalize_check_state(node)
        if s == "skipped":
            continue
        states.append(s)
    if not states:
        return None
    if any(s == "pending" for s in states):
        return "pending"
    if any(s == "failure" for s in states):
        return "errored"   # distinct label; see rendering + CSS below
    if any(s == "success" for s in states):
        return "success"
    return "neutral"
```

Precedence note: `pending` is checked **before** `failure` so a still-running
retry surfaces as "working" rather than flashing an error from a superseded
run. (Dedup already keeps only the latest run per name, so this only matters
across differently-named reviewer checks.)

We map the check-`failure` state to the label `"errored"` deliberately, to keep
it lexically distinct from the review `changes` state everywhere downstream.

### 4. Render a reviewer-check pill

Add a renderer next to `render_bot_pill()` (`pr_core.py:538-543`). It reuses the
bot styling but chooses glyph/class by state, and — importantly — uses a new
`errored` class for check failure so it does not share the red `changes`
styling:

```python
REVIEWER_PILL = {
    "pending": ("pending",  CHECK_GLYPH["pending"], "PR Reviewer running"),
    "errored": ("errored",  "⚠",                    "PR Reviewer check failed"),
    "success": ("approved", CHECK_GLYPH["success"], "PR Reviewer complete"),
    "neutral": ("neutral",  CHECK_GLYPH["neutral"], "PR Reviewer"),
}

def render_reviewer_pill(state):
    cls, glyph, title = REVIEWER_PILL[state]
    return (
        f'<span class="pill bot {cls}" title="{html.escape(title, quote=True)}">'
        f'{BOT_GLYPH} {glyph}</span>'
    )
```

Rendering decisions:

- **success**: the check finishing is not itself a verdict — the verdict comes
  through the bot *review* (`bot_reviews()`), which renders its own pill. Two
  reasonable options; pick one when implementing:
  - (a) render nothing for `success` (return `""`), letting the review pill
    carry the outcome — avoids a redundant green pill; **recommended**.
  - (b) render the neutral/complete pill for visibility even before the review
    lands.
  If (a), have `render_reviewer_pill` return `""` for `success`.
- **errored** uses the `⚠` glyph and a dedicated `errored` class so it reads as
  "the reviewer broke," clearly different from the red ✗ `changes` pill.

### 5. Wire it into `render_pr`

In `render_pr` (`pr_core.py:630`), compute and prepend the reviewer pill to the
bot pills:

```python
rc = reviewer_check_state(pr)
reviewer_pill = render_reviewer_pill(rc) if rc else ""
bot_pills = reviewer_pill + "".join(render_bot_pill(*b) for b in bot_reviews(pr))
```

The Main/E2E `check_pills` line is unchanged in shape — it just no longer
includes the reviewer checks because of the filter in step 2.

### 6. CSS for the distinct "errored" state

Add an `errored` rule to the pill styles (`pr_core.py:488-493`). It must be
visually distinct from both `changes` (red) and `pending`. Use the warning /
amber-ish treatment. If the theme lacks warning tokens, either add
`--warning-*` variables alongside the existing `--failure-*` / `--pending-*`
sets or reuse an existing distinct token:

```css
.pill.errored { background: var(--warning-bg); color: var(--warning-fg); border-color: var(--warning-border); }
```

Confirm the chosen colors differ clearly from `.pill.changes` in both light and
dark mode (check the `--*` definitions and any `prefers-color-scheme` block).

## Edge cases

- **No reviewer checks** → `reviewer_check_state` returns `None`, no pill
  renders. Existing PRs are unaffected.
- **Reviewer check running while an older bot review exists** → pending
  reviewer pill shows alongside the prior review pill; that's correct (a new
  run is in flight).
- **Cancelled / stale reviewer runs** → already collapsed by `_dedupe_contexts`
  and dropped by the `skipped` filter, matching Main-pill behavior.
- **Multiple reviewer checks** (e.g. `PR Reviewer`, `PR Reviewer / lint`) →
  collapsed by the precedence in step 3 (any pending → pending; else any fail →
  errored; else success).

## Testing

- `scripts/` — check for existing tests/fixtures and add cases for
  `bucket_checks` (reviewer checks excluded), `reviewer_check_state`
  (pending / errored / success / none), and rendering.
- Construct fixture PRs with `CheckRun` nodes named `PR Reviewer …` in each
  status (`IN_PROGRESS`; `COMPLETED`+`FAILURE`; `COMPLETED`+`SUCCESS`) and
  assert: (1) they no longer affect the Main pill, and (2) the right bot pill
  class renders.
- Run the server locally and eyeball a PR mid-review to confirm the pending and
  errored pills look distinct from `changes`.
