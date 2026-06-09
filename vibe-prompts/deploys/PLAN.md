# Plan: deploy-PR (integration-branch) handling

## Goal

PRs labeled `integration-branch` are deploy PRs headed for the merge queue. For
these PRs we want to:

1. Drop the reviews pill (review state is irrelevant for a deploy PR).
2. Show a **merge-queue state** pill instead.
3. Make the status **dot purple** with a deploy-specific tooltip.

All work is in `pr_core.py` (Python stdlib, generates static HTML). No new deps.

---

## 1. Fetch the data we need (`PR_FRAGMENT`, lines 50–77)

Add three fields to the GraphQL fragment:

```graphql
labels(first: 20) { nodes { name } }
mergeStateStatus
mergeQueueEntry { state position }
```

- `labels` → detect the `integration-branch` label.
- `mergeStateStatus` → enum incl. `BEHIND` (branch needs update). Note: GitHub
  may return `UNKNOWN` if it hasn't computed mergeability yet, or when the
  viewer lacks push access — treat `UNKNOWN` as "not behind" and fall through.
- `mergeQueueEntry` → `null` when the PR is not in the queue. When present,
  `state` is one of `QUEUED`, `AWAITING_CHECKS`, `MERGEABLE`, `UNMERGEABLE`,
  `LOCKED`. `LOCKED` (typically `position` 0) is the entry currently being
  merged → our "deploying" state.

We already fetch `statusCheckRollup.state` (`SUCCESS` / `FAILURE` / `PENDING` /
`ERROR`), which we need for the "checks passed but not queued" case.

## 2. Detect deploy PRs

Small helper near `review_state` (around line 227):

```python
def is_deploy_pr(pr):
    labels = (pr.get("labels") or {}).get("nodes") or []
    return any((l.get("name") or "") == "integration-branch" for l in labels)
```

## 3. Compute merge-queue state

New helper returning `(state_class, label)` mirroring `review_state`'s shape so
it slots into the existing pill rendering:

```python
def merge_queue_state(pr):
    entry = pr.get("mergeQueueEntry")
    merge_status = pr.get("mergeStateStatus")
    rollup = (pr.get("statusCheckRollup") or {}).get("state")

    # Branch is out of date and must be updated before it can merge.
    if merge_status == "BEHIND":
        return "failure", "Update branch"

    if entry is None:
        # Checks are green but it never got enqueued — something's wrong.
        if rollup == "SUCCESS":
            return "failure", "Not queued"
        return "neutral", "Not queued"

    state = entry.get("state")
    if state == "LOCKED":            # at the front, actively merging/deploying
        return "deploying", "Deploying"
    # QUEUED / AWAITING_CHECKS / MERGEABLE → waiting in the queue
    pos = entry.get("position")
    label = "In queue" if pos is None else f"In queue (#{pos + 1})"
    return "pending", label
```

State → pill-class mapping (per prompt):

| Condition                                   | pill class  | visual        |
|---------------------------------------------|-------------|---------------|
| branch needs update                         | `failure`   | error (red)   |
| checks passed, not in queue                 | `failure`   | error (red)   |
| in the queue (waiting)                      | `pending`   | pending (amber)|
| currently deploying                         | `deploying` | distinct (purple) |
| otherwise (not queued, checks not green)    | `neutral`   | gray          |

## 4. New "deploying" pill style (CSS, ~line 382)

`deploying` is a new class, so add a rule + theme tokens. Use purple to tie it
visually to the purple dot.

In `:root` (light) and the dark `@media` block, add:

```css
--deploy-bg: #f3e8ff; --deploy-fg: #8250df; --deploy-border: #e2c7ff;   /* light */
--deploy-bg: #2d2438; --deploy-fg: #b083f0; --deploy-border: #4c3a66;   /* dark  */
```

And a pill rule alongside the others:

```css
.pill.deploying { background: var(--deploy-bg); color: var(--deploy-fg); border-color: var(--deploy-border); }
```

## 5. Purple dot + tooltip (`render_pr`, lines 462–465; CSS ~line 322/371)

Add a dot color token:

```css
--dot-deploy: #8250df;   /* light */
--dot-deploy: #b083f0;   /* dark  */
```

Add a CSS class:

```css
.status-dot.is-deploy { background: var(--dot-deploy); }
```

In `render_pr`, branch the dot styling/tooltip when it's a deploy PR. Deploy
status takes precedence over draft for the dot color/tooltip:

```python
deploy = is_deploy_pr(pr)
if deploy:
    _, mq_label = mq_state    # reuse computed state for the tooltip
    dot_cls = "status-dot is-deploy"
    dot_title = f' title="Deploy PR — {html.escape(mq_label, quote=True)}"'
elif is_draft:
    dot_cls = "status-dot is-draft"
    dot_title = ' title="Draft"'
else:
    dot_cls = "status-dot"
    dot_title = ""
```

## 6. Swap the pill in `render_pr` (lines 457, 483, 503)

```python
if deploy:
    mq_state = merge_queue_state(pr)          # computed before the dot block
    state_cls, state_label = mq_state
else:
    state_cls, state_label = review_state(pr)
status_pill = f'<span class="pill {state_cls}">{html.escape(state_label)}</span>'
```

Then keep using `status_pill` where `approval` was used in the `.checks` div.
Check pills (Main / E2E) stay as-is for deploy PRs.

---

## Files touched

- `pr_core.py` only:
  - `PR_FRAGMENT` — 3 new fields.
  - New helpers `is_deploy_pr`, `merge_queue_state`.
  - `CSS` — deploy pill tokens/rule, dot token, `.is-deploy` rule.
  - `render_pr` — deploy-aware dot tooltip/color + pill selection.

## Open questions / assumptions

- **"Currently deploying" detection**: assumed `mergeQueueEntry.state == LOCKED`
  (front of queue, being merged). If KA's merge queue surfaces deploy progress
  differently, this mapping may need adjusting.
- **`mergeStateStatus` reliability**: can be `UNKNOWN` until GitHub computes it;
  we treat that as "not behind." Acceptable for a glanceable dashboard.
- Light/dark purple hex values are first-pass picks — easy to tweak.

## Verification

- Run the viewer (`pr_viewer.py` / `pr_server.py`) against an account with at
  least one open `integration-branch` PR and confirm: no reviews pill, correct
  merge-queue pill, purple dot, tooltip text. Non-deploy PRs must be unchanged.
