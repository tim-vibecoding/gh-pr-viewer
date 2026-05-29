# PR Viewer — Plan

## Goal

A script that fetches a GitHub user's open pull requests across all
accessible repos, groups them into stacks/trees, and renders an HTML page
(written to a temp file and opened in the browser) showing each PR with:

1. Three status pills:
   - **Other checks** — rollup of all checks whose name does not match the
     two buckets below.
   - **E2E Tests** — rollup of all checks whose name contains the substring
     `E2E Tests`.
   - **Require Review or Audit Label** — the check with that exact name.
2. Review state: approved / changes requested / commented-without-approval /
   no reviews yet.
3. The PR's position in its stack (rendered as a nested tree).

## Tech stack

- **Python 3** (stdlib only where possible) — picks up the user's existing
  `gh` auth, and gives us proper data structures for tree-building.
- **`gh api graphql`** — one GraphQL query returns every field we need
  (PRs, base/head refs, reviews, statusCheckRollup with per-check
  conclusions). Avoids N+1 calls to `gh pr view`.
- **HTML output** — Python writes a self-contained HTML file (inline CSS,
  no JS framework) to `tempfile.NamedTemporaryFile(suffix=".html",
  delete=False)`, then `webbrowser.open()`s it.
- **CLI surface** — `python pr_viewer.py [--user LOGIN]`. Default user is
  `@me` (the authenticated gh user).

Rejected alternatives:
- Pure shell + `jq`: tree-building and HTML escaping in bash gets gnarly.
- Node: no advantage over Python here; we'd still shell out to `gh`.
- Jinja2 / a web framework: overkill for one static page.

## Data fetching

One GraphQL query, parameterized by `$login`:

```graphql
query($login: String!) {
  user(login: $login) {
    pullRequests(states: OPEN, first: 100,
                 orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes {
        number
        title
        url
        isDraft
        baseRefName
        headRefName
        repository { nameWithOwner defaultBranchRef { name } }
        reviewDecision           # APPROVED | CHANGES_REQUESTED | REVIEW_REQUIRED | null
        latestReviews(last: 20) {
          nodes { author { login } state submittedAt }
        }
        statusCheckRollup {
          state                  # SUCCESS | FAILURE | PENDING | ERROR | EXPECTED
          contexts(last: 100) {
            nodes {
              __typename
              ... on CheckRun    { name conclusion status detailsUrl }
              ... on StatusContext { context state targetUrl }
            }
          }
        }
      }
    }
  }
}
```

Run via:
```bash
gh api graphql -f login="$user" -F query=@query.graphql
```
…or inline the query string in Python and pass with `-f query=...`.

If pagination matters (>100 open PRs), add cursor handling later — start
without it.

## Hierarchy / stack detection

Build a directed edge from each PR to its parent PR using the chosen
signal: **base-branch chain**.

```
parent_of(pr) =
    the PR (in the same repo) whose headRefName == pr.baseRefName,
    if such a PR exists; else None (pr is a root).
```

- Roots are PRs whose base is the repo's default branch (or any branch
  with no matching open PR head).
- Sort children by PR number for stable ordering.
- Render as nested `<ul>`s indented by depth. Each repo's roots become
  top-level entries; PRs from different repos never share a tree.

Edge cases:
- Cycles shouldn't happen in practice, but guard with a visited-set to
  avoid infinite recursion.
- A PR's parent might be closed/merged → it won't be in our open-PR set,
  so we treat it as a root with a small "(base: branchname)" annotation.

## Status check bucketing

For each PR, walk `statusCheckRollup.contexts.nodes` and partition by name
(use `name` for `CheckRun`, `context` for `StatusContext`):

```
bucket = (
    "require_review" if name == "Require Review or Audit Label"
    else "e2e"        if "E2E Tests" in name
    else "other"
)
```

Per bucket, compute a single roll-up status:
- `failure` if any check failed/errored,
- else `pending` if any is queued/in_progress/pending,
- else `success` if at least one succeeded,
- else `neutral` / `none` if the bucket is empty.

Render as a colored pill with the count (e.g. `Other ✓ 12/12`,
`E2E ✗ 1/4`, `Require Review ⏳`).

## Review state

Use `reviewDecision` as the primary signal:
- `APPROVED` → green "Approved"
- `CHANGES_REQUESTED` → red "Changes requested"
- otherwise, look at `latestReviews`:
  - if any review with `state == "COMMENTED"` exists → amber
    "Commented (no approval)"
  - else → grey "No reviews"

(`latestReviews` already deduplicates to the most recent review per
reviewer, which is what we want.)

## HTML rendering

Single template assembled with f-strings. Structure:

```
<html>
  <head><style>… inline CSS …</style></head>
  <body>
    <h1>Open PRs for {user}</h1>
    {for each repo group:}
      <h2>{repo}</h2>
      <ul class="tree">
        {recursive render of PR nodes}
      </ul>
  </body>
</html>
```

Each PR node:
```
<li>
  <span class="pr-title">
    <a href="{url}">#{number}</a> {title} {draft-badge?}
  </span>
  <span class="pills">
    <span class="pill other {state}">Other …</span>
    <span class="pill e2e {state}">E2E …</span>
    <span class="pill require-review {state}">Require Review …</span>
    <span class="pill review {review-state}">…</span>
  </span>
  {children ul if any}
</li>
```

CSS gives pills color by state class (green/amber/red/grey). Tree
indentation via nested `<ul>` margins.

Escape user-controlled strings (title, branch names) with `html.escape`.

## File layout

```
github-pr-viewer/
  PROMPT.md
  PLAN.md
  pr_viewer.py        # main script (single file)
```

Single-file script keeps things easy to share and run.

## Implementation steps

1. Skeleton CLI: `argparse`, `--user` flag defaulting to `@me`.
2. Call `gh api graphql` via `subprocess.run`, parse JSON.
   - If user is `@me`, resolve to `viewer { login }` in the same query
     (use a slightly different query form).
3. Build PR list → group by repo → build parent map → assemble forest.
4. Bucket checks and compute pill states.
5. Compute review state.
6. Render HTML to a temp file, `webbrowser.open()`.
7. Manual smoke test against the user's real PRs.

## Open questions / deferred

- Pagination past 100 open PRs.
- Closed/merged PRs (out of scope per current answers).
- Caching — every run hits the API. Fine for v1.
- Auto-refresh in the browser — not needed; re-run the script.
