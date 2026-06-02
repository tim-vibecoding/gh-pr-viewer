#!/usr/bin/env python3
"""PR Viewer (CLI) — fetch a GitHub user's open PRs, group them into
stacks/trees, render a self-contained HTML page, and open it in the browser.

This is the one-shot command-line entry point. The reusable engine lives in
`pr_core.py`; `pr_server.py` is a long-lived local server built on the same
engine. See vibe-prompts/server/PLAN.md for the design.
"""

import argparse
import sys
import tempfile
import webbrowser

import pr_core


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
    args = parser.parse_args()

    try:
        login, html_doc, count = pr_core.render_page(args.user)
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
