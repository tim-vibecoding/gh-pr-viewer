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
- In server mode, lets you group PRs into **projects** — ordered lists of PRs
  with notes, including closed and merged ones and other people's. See
  [Projects](#projects) below.

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

# Leave off the project chips entirely:
python3 pr_viewer.py --no-projects

# Ignore the 60s cache and fetch fresh:
python3 pr_viewer.py --no-cache
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

# Never cache; every request hits GitHub:
python3 pr_server.py --no-cache
```

Then open `http://127.0.0.1:8765/` in a browser. GitHub data is cached for 60
seconds, so a browser refresh may show data up to a minute old; the **↻
Refresh** button in the nav discards the cache and re-fetches now. Append
`?user=LOGIN` to the URL to view a
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

# Restart it (e.g. after pulling new code):
scripts/restart-server.sh

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

# Restart in one step (or use scripts/restart-server.sh):
launchctl kickstart -k gui/$(id -u)/com.github-pr-viewer.server
```

## Projects

A **project** is an ordered list of PRs with a note on each one — a place to
keep "the four PRs for the Q3 migration, in the order they have to land, with
why". Membership is manual, ordering is yours, and nothing reorders it but you.

Unlike the home page, a project can hold **closed and merged PRs, and other
people's PRs, in any repo you can read** — which is what makes it useful for a
review queue or for a writeup after the fact.

Three pages, all in server mode:

- **Open PRs** (`/`) — the usual list, plus a chip on each row for every
  project it's in, and a **+ Project** control to file it (into an existing
  project or a brand new one) with a note, without leaving the page. The
  header gains an **All / Uncategorized** filter; **Uncategorized** shows only
  the open PRs that aren't in any project, which is the view you work down
  when triaging. Both modes state the ratio, so a filtered view never lies by
  omission.
- **Projects** (`/projects`) — every project, most recently touched first, with
  its PR count split into open/closed and its description. **+ New project**
  creates one inline.
- **Project detail** (`/projects/<id>`) — the main page. Reorder with **▲ ▼**
  and **⤒** (move to top), edit notes in place, rename or describe the
  project, add a PR by pasting a GitHub URL or `owner/repo#123`, and
  **Hide/Show** closed PRs. Whether closed PRs are shown is remembered per
  project and lives in the URL, so a link shows the recipient what you saw.

Every control is a real form or link: no JavaScript is required for anything
(the copy-branch button is still the one progressive extra). Every action posts
and redirects, so refresh is always safe and back/forward behave.

### Where your projects live

Projects are stored in **`projects.json`** next to the code. It's
`.gitignore`d, it's plain JSON you can read and diff, and it's yours to back
up — the notes, descriptions, and ordering in it are the only things in this
app that can't be re-fetched from GitHub. Writes are atomic, and a file the app
can't parse is never overwritten: the home page falls back to a plain PR list
with a warning, and every mutation refuses until the file is readable again.
If it does get damaged, the projects page offers **Start a new projects file**,
which renames the old one to `projects.json.corrupt-1` and starts an empty one
— so you can still open the damaged file in an editor and copy your notes back
out. Nothing is ever deleted.

Point it somewhere else with `PR_VIEWER_STORE`:

```bash
PR_VIEWER_STORE=~/Dropbox/pr-projects.json python3 pr_server.py
```

## How stacks are detected

A PR is treated as a **child** of another open PR (in the same repo) when its
base branch equals that PR's head branch. PRs whose base is the repo's default
branch — or whose base PR is closed/merged — become roots. Roots based on a
non-default branch get a small `(base: branchname)` annotation. Children are
sorted by PR number for stable ordering.

## Limitations

- Only **open** PRs are fetched for the PR list (no closed/merged). Projects
  are the exception: they fetch their own entries in any state.
- Capped at the first **100** open PRs per user — there's no pagination yet.
- Fetches are cached on disk for **60 seconds**, so data can be up to a minute
  stale and nothing on the page says how old it is. **↻ Refresh** (server) or
  `--no-cache` (either entry point) forces a fetch. The cache lives in
  `.pr-cache/` next to the code (gitignored, entirely re-derivable); override
  the location with `PR_VIEWER_CACHE` and the TTL with `PR_VIEWER_CACHE_TTL`.
  Errors are never cached and stale entries are never served in their place, so
  a GitHub outage still shows today's error page once the TTL lapses.
- The **projects index issues two GitHub fetches** (one for every project's
  entries, one for your open PRs to count the uncategorized ones); both degrade
  to a partial page rather than an error if they fail. Cached per PR, so the
  index warms every project page behind it.
- No live auto-refresh: the CLI is one-shot (re-run to update), and in server
  mode a browser refresh re-renders (no JS/websockets pushing updates).
- The **CLI renders project chips but no controls** — it writes a static file,
  so forms would post nowhere and filter links would 404. Use server mode to
  actually manage projects, or `--no-projects` to drop the chips too.

## Project layout

```
github-pr-viewer/
  pr_viewer.py            # CLI entry point (one-shot render + open browser)
  pr_server.py            # local HTTP server: routing, mutations, redirects
  pr_core.py              # shared engine: fetch, process, render HTML
  pr_projects.py          # projects index / detail pages + the home additions
  pr_store.py             # projects.json: load, save, project & entry CRUD
  pr_cache.py             # the 60s on-disk fetch cache: get, put, clear
  projects.json           # your projects (gitignored; created on first use)
  .pr-cache/              # cached GitHub data (gitignored; safe to delete)
  test_pr_core.py         # checks, review state, pill combinations
  test_pr_fetch.py        # the batched by-reference GitHub fetch + caching
  test_pr_store.py        # storage, ordering, atomic writes, corruption
  test_pr_cache.py        # TTL, negative caching, corruption, clear
  test_pr_projects.py     # project page rendering
  test_pr_home.py         # chips, the uncategorized filter, degradation
  test_pr_server.py       # routing, POST/redirect/flash, cross-origin refusal
  scripts/
    install-launchagent.sh  # install/remove the macOS login LaunchAgent
    restart-server.sh       # restart the running LaunchAgent
  README.md               # this file
  vibe-prompts/initial-creation/
    PROMPT.md             # the prompt that kicked it off
    PLAN.md               # the AI-generated design doc
  vibe-prompts/server/
    PROMPT.md             # the prompt for the CLI/server split
    PLAN.md               # the plan for that change
  vibe-prompts/projects/
    PROMPT.md             # the prompt for projects
    UI.md                 # how the feature should operate
    PLAN.md               # how the code gets there
  vibe-prompts/caching/
    PLAN.md               # the plan for the fetch cache
```

## Tests

Standard library `unittest`, no dependencies, no network — the GitHub calls are
patched out:

```bash
python3 -m unittest discover
```
