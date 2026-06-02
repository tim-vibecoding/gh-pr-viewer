# Plan: Run the PR viewer as a CLI *or* a local server

Today `pr_viewer.py` is one file that does everything: fetch → process → render
HTML → write a temp file → open the browser. We want to keep that command-line
behavior working exactly as-is, while also being able to start a long-lived
local server that renders the same page on each HTTP request.

The work is in three stages, matching the prompt:

1. Extract the reusable logic into a shared module.
2. Build a small stdlib HTTP server on top of it.
3. Update the README to document the server option.

Stdlib only, no new dependencies — that's the project's whole ethos.

## Stage 1 — Extract the core into `pr_core.py`

Create a new module `pr_core.py` that holds everything *except* the
CLI-specific glue (argparse, tempfile, `webbrowser`). `pr_viewer.py` keeps its
`main()` and imports from it.

**Moves verbatim into `pr_core.py`:**

- Query constants: `QUERY_VIEWER`, `QUERY_USER`, `PR_FRAGMENT`,
  `REQUIRE_REVIEW_CHECK`, `E2E_SUBSTRINGS`.
- Fetching: `fetch_prs`.
- Processing: `_check_name`, `_check_timestamp`, `_dedupe_contexts`,
  `_bucket_for`, `_normalize_check_state`, `bucket_checks`, `review_state`,
  `build_forest`.
- Rendering: `CSS`, `CHECK_GLYPH`, `render_pill`, `render_pr`, `render_html`,
  `_walk`.

**Stays in `pr_viewer.py`:** `main()` and its imports (`argparse`, `tempfile`,
`webbrowser`).

**One required refactor — stop calling `sys.exit` in the core.** `fetch_prs`
currently calls `sys.exit(...)` on every failure (missing `gh`, GraphQL errors,
unknown user). That's fine for a one-shot CLI but fatal for a server — a single
bad request would kill the process. So:

- Add `class PRViewerError(Exception): pass` to `pr_core.py`.
- Replace each `sys.exit("error: ...")` in `fetch_prs` with
  `raise PRViewerError("...")` (drop the `sys.exit`-style `error:` prefix; let
  each caller decide how to present it).
- `pr_core.py` no longer needs `import sys`.

**Add one convenience function** so both front-ends share the same pipeline and
neither duplicates the fetch→build→render sequence:

```python
def render_page(user):
    """Fetch `user`'s PRs and return (login, html_doc, pr_count).

    Raises PRViewerError on any fetch/GraphQL failure.
    """
    login, prs = fetch_prs(user)
    repo_groups = build_forest(prs)
    return login, render_html(login, repo_groups), len(prs)
```

`pr_viewer.py`'s `main()` becomes a thin wrapper:

```python
import pr_core

def main():
    parser = argparse.ArgumentParser(...)   # --user, --no-open unchanged
    ...
    args = parser.parse_args()
    try:
        login, html_doc, count = pr_core.render_page(args.user)
    except pr_core.PRViewerError as e:
        sys.exit(f"error: {e}")
    # write temp file + webbrowser.open(), exactly as today
```

This is a pure refactor — the existing `python3 pr_viewer.py [--user X]
[--no-open]` invocations behave identically. (Smoke-test that before moving on.)

## Stage 2 — The server

`pr_viewer.py` stays exactly as it is today — the one-shot CLI, no new flags.
The server is its own runnable script, `pr_server.py`, with its own
`__main__` / argparse. So the two entry points are:

```bash
python3 pr_viewer.py [--user X] [--no-open]   # one-shot, unchanged
python3 pr_server.py [--user X] [--port N] [--host H]   # long-lived server
```

Both import the shared `pr_core`; neither knows about the other. That's the
cleanest split — `pr_viewer.py` doesn't grow a server branch it has to carry,
and the server isn't gated behind a flag on an otherwise unrelated command.

`pr_server.py` CLI flags:

- `--user X` — default `@me`, same as the CLI.
- `--port N` — default `8765`.
- `--host H` — default `127.0.0.1` (loopback only; this is a personal tool, we
  don't want to bind `0.0.0.0` by default).

Implementation: a small stdlib `http.server.HTTPServer` +
`BaseHTTPRequestHandler`:

```python
# pr_server.py
import argparse
import html
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
import pr_core

def serve(user, host="127.0.0.1", port=8765):
    handler = _make_handler(user)
    httpd = HTTPServer((host, port), handler)
    print(f"Serving PRs for {user} at http://{host}:{port}/  (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        httpd.server_close()

def main():
    parser = argparse.ArgumentParser(description="Serve a GitHub user's open PRs locally.")
    parser.add_argument("--user", default="@me")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve(args.user, host=args.host, port=args.port)

if __name__ == "__main__":
    main()
```

The request handler, per `GET`:

- `/` → re-fetch and re-render on **every** request (always fresh — this is how
  we get "refresh the browser to update" for free). A `?user=LOGIN` query-string
  param overrides the default user so you can browse other people's PRs without
  restarting; otherwise fall back to the user the server was started with.
- `/favicon.ico` → `204 No Content` (avoid a noisy 404 + a wasted GitHub fetch).
- Anything else → `404`.
- On `PRViewerError`, return `500` with the message HTML-escaped into a minimal
  page rather than a stack trace.
- Override `log_message` to a concise one-line log (or quiet it) instead of the
  default stderr spew.

We don't auto-open the browser in serve mode by default (the server prints its
URL); opening once on startup could be a nice touch but is optional and easy to
add later.

### Why this shape

- **`http.server`, not Flask/FastAPI** — stdlib only, and a single static page
  per request needs nothing more. Matches the no-`pip-install` promise.
- **Re-fetch per request, no caching** — simplest correct behavior, and it
  turns the existing "no auto-refresh" limitation into "just hit refresh." If
  GitHub rate limits become an issue we can add a short TTL cache later.
- **Loopback by default** — the page exposes your PR activity; don't bind all
  interfaces unless the user opts in via `--host 0.0.0.0`.
- **Two independent entry points** — `pr_viewer.py` stays the CLI; `pr_core.py`
  is the shared engine; `pr_server.py` is its own runnable server. Neither
  front-end depends on the other; both depend only on `pr_core`.

## Stage 3 — README

Update `README.md`:

- **Intro / "What it does"** — add a line that it can also run as a local server
  that re-renders on each request.
- **Usage** — new subsection **"Running as a local server"**:

  ```bash
  # Start a local server (defaults to 127.0.0.1:8765):
  python3 pr_server.py

  # Pick a port / user:
  python3 pr_server.py --port 9000 --user octocat
  ```

  Note that you then open `http://127.0.0.1:8765/` in a browser, that each page
  load re-fetches from GitHub (so refresh = update), and that `?user=LOGIN` in
  the URL overrides the default user.
- **Limitations** — soften the "No auto-refresh" bullet: in server mode a browser
  refresh re-fetches; the 100-PR cap and no-caching notes still stand (and note
  every server request hits the API).
- **Project layout** — update the tree:

  ```
  github-pr-viewer/
    pr_viewer.py            # CLI entry point (one-shot render + open browser)
    pr_server.py            # local HTTP server entry point (stdlib http.server)
    pr_core.py              # shared engine: fetch, process, render HTML
    README.md
    vibe-prompts/server/
      PROMPT.md             # the prompt for this change
      PLAN.md               # this plan
  ```

- The existing "vibecoded" disclaimer stays; optionally extend its file
  references to mention the new modules.

## Verification

1. **CLI unchanged:** `python3 pr_viewer.py --no-open` still prints
   `Wrote N PR(s) ...` and writes a valid HTML file; `--user octocat` works.
2. **Server:** `python3 pr_server.py`, open `http://127.0.0.1:8765/`, confirm
   the same page renders; refresh re-fetches; `/?user=octocat` switches users;
   `Ctrl-C` shuts down cleanly.
3. **Error path:** `python3 pr_server.py --user no-such-user-xyz` (or a bad
   fetch) returns a 500 page with the error message, and the server keeps
   running.

## Out of scope

- Caching / rate-limit handling (re-fetch every request for now).
- Pagination past 100 PRs (unchanged from today).
- Auth, TLS, or exposing beyond loopback by default.
- Auto-refresh via JS / websockets — a manual browser refresh is enough.
