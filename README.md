# PR Viewer

A single-file Python script that fetches your open GitHub pull requests,
groups them into stacks/trees, and renders a self-contained HTML page that
opens in your browser.

> ⚠️ **This project was vibecoded.** It was built start-to-finish by prompting
> an AI agent (Claude) — the design (`vibe-prompts/initial-creation/PLAN.md`)
> and the implementation (`pr_viewer.py`) were both AI-generated from the
> prompt in `vibe-prompts/initial-creation/PROMPT.md`. Read it with that in
> mind: it works, but it hasn't had the scrutiny of hand-written code. Use at
> your own risk.

The README was also vibecoded except for this sentence.

## What it does

- Fetches all **open** PRs for a GitHub user (default: the authenticated user).
- Groups PRs by repository and reconstructs **stacks/trees** by chaining each
  PR's base branch to the head branch of another open PR.
- For each PR, shows three status pills rolling up its checks:
  - **Other** — every check not covered by the two buckets below.
  - **E2E** — checks whose name contains `E2E Tests`.
  - **Require Review** — the check named `Require Review or Audit Label`.
- Shows the **review state**: Approved, Changes requested, Commented
  (no approval), or No reviews.
- Renders everything as a static HTML page (inline CSS, light/dark aware, no
  JavaScript) written to a temp file and opened in your browser.

## Requirements

- **Python 3** (standard library only — no `pip install` needed).
- The **[GitHub CLI](https://cli.github.com/)** (`gh`) installed and
  authenticated. The script shells out to `gh api graphql` and reuses your
  existing `gh` auth.

Make sure you're logged in first:

```bash
gh auth login
```

## Usage

```bash
# Your own open PRs (opens in the browser):
python3 pr_viewer.py

# A specific user's open PRs:
python3 pr_viewer.py --user octocat

# Write the HTML file but don't open a browser:
python3 pr_viewer.py --no-open
```

The script prints the path to the generated HTML file, e.g.:

```
Wrote 7 PR(s) for yourname to /var/folders/.../tmpXXXX.html
```

## How stacks are detected

A PR is treated as a **child** of another open PR (in the same repo) when its
base branch equals that PR's head branch. PRs whose base is the repo's default
branch — or whose base PR is closed/merged — become roots. Roots based on a
non-default branch get a small `(base: branchname)` annotation. Children are
sorted by PR number for stable ordering.

## Limitations

- Only **open** PRs are fetched (no closed/merged).
- Capped at the first **100** open PRs per user — there's no pagination yet.
- No caching; every run hits the GitHub API.
- No auto-refresh — re-run the script to update.

## Project layout

```
github-pr-viewer/
  pr_viewer.py                          # the whole script
  README.md                             # this file
  vibe-prompts/initial-creation/
    PROMPT.md                           # the prompt that kicked it off
    PLAN.md                             # the AI-generated design doc
```
