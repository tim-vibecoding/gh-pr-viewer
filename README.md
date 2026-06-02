# PR Viewer

A single-file Python script that fetches your open GitHub pull requests,
groups them into stacks/trees, and renders a self-contained HTML page that
opens in your browser.

> ⚠️ **This project was vibecoded.** It was built start-to-finish by prompting
> an AI agent (Claude) — the design (`vibe-prompts/initial-creation/PLAN.md`)
> and the implementation (`pr_viewer.py`, `pr_server.py`, `pr_core.py`) were
> both AI-generated from the prompts in `vibe-prompts/`. Read it with that in
> mind: it works, but it hasn't had the scrutiny of hand-written code. Use at
> your own risk.

The README was also vibecoded except for this sentence.

## What it does

- Fetches all **open** PRs for a GitHub user (default: the authenticated user).
- Groups PRs by repository and reconstructs **stacks/trees** by chaining each
  PR's base branch to the head branch of another open PR.
- For each PR, shows two status pills rolling up its checks:
  - **Main** — every check not covered by the E2E bucket.
  - **E2E** — checks whose name contains `E2E Tests`.
  - The `Require Review or Audit Label` check is filtered out entirely.
- Shows the **review state**: Approved, Changes requested, Commented
  (no approval), or No reviews.
- Renders everything as a static HTML page (inline CSS, light/dark aware, no
  JavaScript) written to a temp file and opened in your browser.
- Can also run as a **local server** that re-renders the same page on each HTTP
  request — refresh the browser to get the latest state.

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

### Running as a local server

Instead of a one-shot render, you can run a long-lived local server that
re-fetches and re-renders on every request:

```bash
# Start a local server (defaults to 127.0.0.1:8765):
python3 pr_server.py

# Pick a port / user:
python3 pr_server.py --port 9000 --user octocat
```

Then open `http://127.0.0.1:8765/` in a browser. Each page load re-fetches from
GitHub, so **refresh = update**. Append `?user=LOGIN` to the URL to view a
different user's PRs without restarting the server (e.g.
`http://127.0.0.1:8765/?user=octocat`). The server binds to loopback
(`127.0.0.1`) by default; pass `--host 0.0.0.0` only if you really want to
expose it. Press `Ctrl-C` to stop.

### Running the server at login (macOS)

To keep the server running in the background and start it automatically every
time you log in, install it as a [LaunchAgent](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html).
A helper script generates the `launchd` plist and loads it for you:

```bash
# Install with defaults (your own PRs, 127.0.0.1:8765):
scripts/install-launchagent.sh

# Pick a user / port:
scripts/install-launchagent.sh --user octocat --port 9000

# Remove it later:
scripts/install-launchagent.sh --uninstall
```

The script writes `~/Library/LaunchAgents/com.github-pr-viewer.server.plist`
and loads it immediately, so the server starts now and on every subsequent
login. `KeepAlive` is set, so `launchd` restarts the server if it ever exits.
Output is logged to `pr_server.log` in the repo directory.

Once it's running, just open `http://127.0.0.1:8765/` (or your chosen port).
The agent reuses your existing `gh` auth, so make sure you've run
`gh auth login` first.

A couple of useful `launchctl` commands:

```bash
# Check it's loaded:
launchctl list | grep github-pr-viewer

# Stop/start without uninstalling:
launchctl unload ~/Library/LaunchAgents/com.github-pr-viewer.server.plist
launchctl load   ~/Library/LaunchAgents/com.github-pr-viewer.server.plist
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
- No caching; every CLI run — and every server request — hits the GitHub API.
- No live auto-refresh: the CLI is one-shot (re-run to update), and in server
  mode a browser refresh re-fetches (no JS/websockets pushing updates).

## Project layout

```
github-pr-viewer/
  pr_viewer.py            # CLI entry point (one-shot render + open browser)
  pr_server.py            # local HTTP server entry point (stdlib http.server)
  pr_core.py              # shared engine: fetch, process, render HTML
  scripts/
    install-launchagent.sh  # install/remove the macOS login LaunchAgent
  README.md               # this file
  vibe-prompts/initial-creation/
    PROMPT.md             # the prompt that kicked it off
    PLAN.md               # the AI-generated design doc
  vibe-prompts/server/
    PROMPT.md             # the prompt for the CLI/server split
    PLAN.md               # the plan for that change
```
