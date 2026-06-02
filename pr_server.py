#!/usr/bin/env python3
"""PR Viewer (server) — serve a GitHub user's open PRs as a local web page.

A small stdlib HTTP server that re-fetches and re-renders on every request, so
a browser refresh always shows the current state. The reusable engine lives in
`pr_core.py`; `pr_viewer.py` is the one-shot CLI built on the same engine.

See vibe-prompts/server/PLAN.md for the design.
"""

import argparse
import html
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import pr_core


def _error_page(message):
    """Minimal HTML error page with the message safely escaped."""
    return (
        "<!DOCTYPE html>"
        '<html lang="en"><head><meta charset="utf-8">'
        "<title>PR Viewer — error</title></head><body>"
        "<h1>Something went wrong</h1>"
        f"<pre>{html.escape(message)}</pre>"
        "</body></html>"
    )


def _make_handler(default_user):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, body, content_type="text/html; charset=utf-8"):
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            parsed = urlparse(self.path)

            if parsed.path == "/favicon.ico":
                # No favicon; 204 avoids a noisy 404 + a wasted GitHub fetch.
                self.send_response(204)
                self.end_headers()
                return

            if parsed.path != "/":
                self._send(404, _error_page("Not found"))
                return

            # ?user=LOGIN overrides the user the server was started with.
            params = parse_qs(parsed.query)
            user = params.get("user", [default_user])[0]

            try:
                _login, html_doc, _count = pr_core.render_page(user)
            except pr_core.PRViewerError as e:
                self._send(500, _error_page(str(e)))
                return

            self._send(200, html_doc)

        def log_message(self, format, *args):
            # Concise one-line log instead of the default stderr spew.
            print(f"{self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

    return Handler


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
    parser = argparse.ArgumentParser(
        description="Serve a GitHub user's open PRs locally."
    )
    parser.add_argument("--user", default="@me")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve(args.user, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
