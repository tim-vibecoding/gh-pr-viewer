# Plan: Reduce visual noise in the PR viewer

All changes are in `pr_viewer.py`. Four targeted edits, no behavior changes to data
fetching or stack detection — purely presentational.

## Current layout (per PR)

Everything sits in a single wrapping flex row (`.pr-row`):

```
#11492 Make assessments… (base: deploy/ax-next)  [Other ✗ 37/42] [E2E ✓ 11/11] [Require Review ✓] [Commented (no approval)]
```

The title, the three check pills, and the approval pill all flow together and wrap
arbitrarily, which is the main source of clutter.

## Target layout

```
#11492 Make assessments… (base: deploy/ax-next)   [Commented]
   [Other ✗ 37/42]  [E2E]  [Require Review]
```

- Approval status sits on the title row (right side).
- Check pills drop to their own row beneath.
- Passing checks show no count.
- More breathing room between PRs.

## Changes

### 1. Omit counts for passing checks — `render_pill`

In `render_pill` (lines ~295–304), only append the `passed/total` count when the
check is **not** passing, so a green check reads `E2E ✓` instead of `E2E ✓ 11/11`.
Keep the count for `failure` and `pending` states (e.g. `Other ✗ 37/42`), where the
ratio is informative.

```python
def render_pill(label, info):
    glyph = CHECK_GLYPH[info["state"]]
    # Counts only add signal when something isn't passing.
    if info["total"] and info["state"] != "success":
        count = f" {info['passed']}/{info['total']}"
    else:
        count = ""
    ...
```

(Optionally also drop the glyph for `success` since color already conveys it — but
keeping `✓` is fine and lower-risk. Leave the glyph as-is.)

### 2. Move approval status to the top row — `render_pr` + CSS

Split the single pill group into two:

- **Title row:** title + `draft` + `base-note` + the approval pill.
- **Checks row:** the three check pills (Other / E2E / Require Review) only.

In `render_pr` (lines ~324–348), build the approval pill separately from the check
pills, place it inside `.pr-row` after `base_note`, and emit the check pills in a new
sibling block below `.pr-row`:

```python
approval = f'<span class="pill {rstate}">{html.escape(rlabel)}</span>'
check_pills = "".join([
    render_pill("Other", checks["other"]),
    render_pill("E2E", checks["e2e"]),
    render_pill("Require Review", checks["require_review"]),
])
return (
    '<li class="pr">'
    '<div class="pr-row">'
    f'<span class="pr-title"><a href="{url}">#{number}</a> {title}</span>'
    f'{draft}{base_note}{approval}'
    '</div>'
    f'<div class="checks">{check_pills}</div>'
    f'{children_html}'
    '</li>'
)
```

CSS: add a `.checks` rule that mirrors the old `.pills` flex layout and gives a small
top gap so it reads as a secondary row:

```css
.checks { display: flex; flex-wrap: wrap; gap: .35rem; margin-top: .25rem; }
```

The existing `.pills` selector can be removed once it's unused (or kept harmlessly).

### 3. Shorten the "Commented" label — `review_state`

In `review_state` (line ~198), change the commented label from
`"Commented (no approval)"` to `"Commented"`. (The amber color already signals it's
not an approval, so the parenthetical is redundant.)

### 4. More whitespace, especially between PRs — CSS

Tune spacing in the `CSS` block:

- Increase `li.pr` vertical margin from `.5rem` to roughly `1rem` so sibling PRs are
  clearly separated.
- Slightly increase the nested-tree indent/left padding spacing if it feels cramped
  after the row split (optional).
- Keep the `h2` repo-group spacing as-is (already `margin-top: 2rem`), or bump to
  `2.5rem` for a touch more separation between repos.

```css
li.pr { margin: 1rem 0; }
```

## Verification

Run `python3 pr_viewer.py --user timmcca-be` (or `--no-open` to just write the file)
and confirm:

- Passing pills show no count; failing/pending pills still show `n/m`.
- Approval pill is on the title row; check pills are on their own row below.
- Commented PRs read just `Commented`.
- Visibly more space between PRs.

## Out of scope

No changes to GraphQL queries, bucketing logic, or stack detection. Dark-mode pill
colors already cover all states and need no edits.
