#!/usr/bin/env python3
"""PR Viewer (CLI) — fetch a GitHub user's open PRs, group them into
stacks/trees, render a self-contained HTML page, and open it in the browser.

This is the one-shot command-line entry point. The reusable engine lives in
`pr_core.py`; `pr_server.py` is a long-lived local server built on the same
engine. See vibe-prompts/server/PLAN.md for the design.

The page it writes is a static file, so it renders project **chips** —
read-only and genuinely useful — but none of the projects controls: forms
would post nowhere and filter links would 404. `--no-projects` gets you the
pre-projects output.
"""

import argparse
import sys
import tempfile
import webbrowser

import pr_cache
import pr_core
import pr_projects
import pr_store


def main():
    parser = argparse.ArgumentParser(
        description="Fetch a GitHub user's open PRs and render them as an HTML tree."
    )
    parser.add_argument(
        "--user",
        default="@me",
        help="GitHub login to fetch PRs for (default: the authenticated user).",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Write the HTML file but don't open it in a browser.",
    )
    parser.add_argument(
        "--no-projects",
        action="store_true",
        help="Omit project chips (the output this produced before projects existed).",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the cache entirely for this run; always fetch from GitHub.",
    )
    args = parser.parse_args()
    # Caching is an entry-point policy, not a library default: it's live where
    # a person runs the app, and off for anything that imports pr_core.
    pr_cache.set_enabled(not args.no_cache)

    try:
        if args.no_projects:
            login, html_doc, count = pr_core.render_page(args.user)
        else:
            login, prs = pr_core.fetch_prs(args.user)
            store, store_error = pr_store.load()
            html_doc, count = pr_projects.render_home(
                login, prs, store, store_error, {}, interactive=False
            ), len(prs)
    except pr_core.PRViewerError as e:
        sys.exit(f"error: {e}")

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8"
    ) as f:
        f.write(html_doc)
        path = f.name

    print(f"Wrote {count} PR(s) for {login} to {path}")
    if not args.no_open:
        webbrowser.open(f"file://{path}")


if __name__ == "__main__":
    main()
