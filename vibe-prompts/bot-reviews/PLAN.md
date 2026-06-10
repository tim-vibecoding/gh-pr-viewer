# Plan: Handle reviews from bots

## Goal

Bots (reviewers whose login contains `[bot]`, e.g. `github-actions[bot]`,
`dependabot[bot]`) should be treated separately from human reviewers:

1. **Bot reviews must not affect the overall (human) approval status** shown in
   the review pill.
2. **Bot reviews get their own indicator** in the `.checks` row:
   - approved by bot → 🤖 followed by ✓
   - commented by bot → 🤖 followed by 💬
   - changes requested by bot → 🤖 followed by ✗

All work is in `pr_core.py`. No new dependencies — the project renders pure
HTML/CSS with Unicode glyphs (see `CHECK_GLYPH`, lines 450‑456).

## Background (current behavior)

- `review_state(pr)` (lines 230‑259) computes `(state_class, label)` for the
  human review pill. It keys off `pr["reviewDecision"]` (GitHub's aggregate) and
  `pr["latestReviews"].nodes` (each has `author.login`, `state`, `submittedAt`).
- `render_pr` (lines 502‑565) renders the status pill at line 542 inside the
  `<div class="checks">` (line 562), after the Main/E2E check pills.
- Bot reviews are currently lumped in with everything else, so a bot's
  `COMMENTED` review can surface as "Commented" and a bot's `CHANGES_REQUESTED`
  could leak into the decision.

## Implementation steps

### 1. Add a bot detector

```python
def is_bot(login):
    return "[bot]" in (login or "")
```

### 2. Exclude bots from human review state

In `review_state`, filter bot authors out of `reviews` before computing
anything:

```python
reviews = [
    r for r in ((pr.get("latestReviews") or {}).get("nodes") or [])
    if not is_bot((r.get("author") or {}).get("login"))
]
```

Caveat about `reviewDecision`: it is GitHub's aggregate and *could* in principle
reflect a bot review. In practice bots are not required/CODEOWNER reviewers, so
`reviewDecision` is driven by humans. To be safe and honor "bots must not affect
approval status", guard the `APPROVED` / `CHANGES_REQUESTED` branches so they
only fire when there is at least one matching **human** review backing the
decision; otherwise fall through to the commented/none logic. This keeps the
decision human-only without re-deriving CODEOWNERS rules.

### 3. Compute bot review state separately

Add a helper that summarizes bot reviews into zero or more indicators. Use the
latest review per bot (`submittedAt`) so an old comment isn't shown alongside a
newer approval. Map each bot's current state to a `(state_class, glyph)`:

| Bot review state    | state_class | glyph |
|---------------------|-------------|-------|
| `APPROVED`          | `approved`  | ✓     |
| `CHANGES_REQUESTED` | `changes`   | ✗     |
| `COMMENTED`         | `commented` | 💬    |

```python
BOT_GLYPH = "🤖"
SPEECH_GLYPH = "💬"

def bot_reviews(pr):
    """Return [(state_class, glyph, login), ...] — one per bot, latest review."""
    latest = {}  # login -> review node (most recent by submittedAt)
    for r in (pr.get("latestReviews") or {}).get("nodes") or []:
        login = (r.get("author") or {}).get("login")
        if not is_bot(login):
            continue
        if login not in latest or (r.get("submittedAt") or "") > (latest[login].get("submittedAt") or ""):
            latest[login] = r
    out = []
    for login, r in latest.items():
        state = r.get("state") or ""
        if state == "APPROVED":
            out.append(("approved", CHECK_GLYPH["success"], login))
        elif state == "CHANGES_REQUESTED":
            out.append(("changes", CHECK_GLYPH["failure"], login))
        elif state == "COMMENTED":
            out.append(("commented", SPEECH_GLYPH, login))
    return out
```

(`latestReviews` already returns the latest review per author, so the dedup is a
safety net; it's cheap and harmless.)

### 4. Render bot pills

Add a small renderer and call it from `render_pr`, placing bot pills after the
human status pill:

```python
def render_bot_pill(state_cls, glyph, login):
    title = html.escape(f"{login} {state_cls}", quote=True)
    return (
        f'<span class="pill bot {state_cls}" title="{title}">'
        f'{BOT_GLYPH} {glyph}</span>'
    )
```

In `render_pr`, after building `status_pill` (line 542):

```python
bot_pills = "".join(render_bot_pill(*b) for b in bot_reviews(pr))
```

and include `{bot_pills}` in the `.checks` div (line 562), after
`{status_pill}`.

Note: deploy PRs use `merge_queue_state` instead of `review_state`. Decide
whether bot pills should show on deploy PRs too — simplest and most consistent
is to render `bot_pills` regardless of deploy status, since it's orthogonal to
merge-queue state. (Bots rarely review deploy PRs, so this is low-risk.)

### 5. Styling

The `.bot` pills reuse existing color classes (`approved`/`changes`/
`commented`), so they get correct colors for free from lines 428‑431. Add only a
minor tweak so the emoji sits nicely — optional `.pill.bot { padding-left:.4rem;
padding-right:.4rem; }`. No new CSS variables needed.

## Edge cases

- **Multiple bots** (e.g. one approves, one comments): one pill each, deduped by
  login.
- **Bot + humans**: human pill reflects only humans; bot pill(s) appear
  alongside.
- **Bot only, no humans**: human pill shows "No reviews"; bot pill shows the bot
  result. This is the desired behavior — a bot approval is not a human approval.
- **`[bot]` in a human name**: extremely unlikely; GitHub appends `[bot]` only to
  app accounts, so the substring check is reliable.

## Testing / verification

- Check for existing tests: `ls` the repo for a `test_*.py` / `tests/`. If
  present, add cases for `is_bot`, `review_state` excluding bots, and
  `bot_reviews` for each of the three states.
- Manual: run the viewer against a user/repo with known bot reviews (or craft a
  fixture PR node) and confirm the rendered `.checks` row shows 🤖✓ / 🤖💬 /
  🤖✗ and that the human pill is unaffected.

## Summary of edits to `pr_core.py`

1. Add `is_bot`, `BOT_GLYPH`, `SPEECH_GLYPH`, `bot_reviews`, `render_bot_pill`.
2. Filter bots out of `review_state` and guard the decision branches to be
   human-only.
3. Render bot pills in `render_pr`'s `.checks` div.
4. (Optional) one small `.pill.bot` CSS rule.
