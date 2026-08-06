#!/usr/bin/env python3
"""PR Viewer (server) — serve a GitHub user's open PRs as a local web page.

A small stdlib HTTP server that re-fetches and re-renders on every request, so
a browser refresh always shows the current state. The reusable engine lives in
`pr_core.py`; `pr_viewer.py` is the one-shot CLI built on the same engine;
`pr_projects.py` renders the projects pages and `pr_store.py` holds their data.

Every mutating route ends in `303 See Other`. There is no POST result page to
re-submit, so refresh is always safe, back/forward behave, and each action
lands on a URL that fully describes the view.

See vibe-prompts/server/PLAN.md and vibe-prompts/projects/PLAN.md.
"""

import argparse
import html
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs, urlencode, unquote

import pr_core
import pr_projects
import pr_store

# A form post that needs more than this is not a form post.
MAX_BODY = 64 * 1024

PROJECT_RE = re.compile(r"^/projects/([^/]+)$")
PROJECT_DELETE_RE = re.compile(r"^/projects/([^/]+)/delete$")

# Query keys the flash owns. A redirect replaces them wholesale rather than
# stacking a second flash on top of the one already in `return_to`.
FLASH_KEYS = ("flash", "pr", "repo", "project", "text", "name", "desc")


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


# ---------------------------------------------------------------------------
# Redirect building
# ---------------------------------------------------------------------------

def _is_safe_path(target):
    """Only same-origin absolute paths. `return_to` arrives in a form body, so
    an open redirect would be one crafted page away."""
    if not target or not target.startswith("/") or target.startswith("//"):
        return False
    parsed = urlparse(target)
    return not parsed.scheme and not parsed.netloc


def redirect_url(return_to, fallback, anchor=None, **flash):
    """`return_to` (or `fallback`) with the flash code and its arguments
    merged in, and an anchor so the page lands on the row you acted from."""
    target = return_to if _is_safe_path(return_to) else fallback
    parsed = urlparse(target)
    params = {
        k: v for k, v in parse_qs(parsed.query).items() if k not in FLASH_KEYS
    }
    for key, value in flash.items():
        if value in (None, "", []):
            continue
        params[key] = value if isinstance(value, list) else [str(value)]
    query = urlencode(params, doseq=True)
    out = parsed.path + ("?" + query if query else "")
    if anchor:
        out += "#" + anchor
    return out


# ---------------------------------------------------------------------------
# POST handlers
#
# Each takes the parsed form and returns a Location. They never raise: a
# `pr_store` failure becomes a flash code on the redirect and the server keeps
# running.
# ---------------------------------------------------------------------------

def _field(form, key, default=""):
    values = form.get(key) or []
    return values[0] if values else default


def _int_field(form, key):
    try:
        return int(_field(form, key))
    except ValueError:
        return None


def _loaded(return_to, fallback):
    """(store, None) or (None, error_redirect). A store we couldn't parse is
    never written over — the mutation refuses loudly instead."""
    store, error = pr_store.load()
    if error:
        return None, redirect_url(return_to, fallback, flash="unreadable")
    return store, None


def _saved(store, location, return_to, fallback):
    try:
        pr_store.save(store)
    except pr_store.StoreError:
        return redirect_url(return_to, fallback, flash="savefail")
    return location


def post_create(form):
    """Create, and go straight to the new project's page — the next thing you
    do is add PRs."""
    return_to, fallback = _field(form, "return_to"), pr_projects.INDEX_PATH
    name = _field(form, "name").strip()
    description = _field(form, "description")
    store, refused = _loaded(return_to, fallback)
    if refused:
        return refused
    if not name:
        return redirect_url(None, fallback, flash="noname", desc=description)
    # A duplicate name warns rather than blocking: a rename is cheap and a hard
    # error mid-flow is not.
    if not _field(form, "confirm") and pr_store.name_exists(store, name):
        return redirect_url(None, fallback, anchor="new-project",
                            flash="dupname", name=name, desc=description)
    project = pr_store.create_project(store, name, description)
    return _saved(store, pr_projects.project_url(project["id"], flash="created"),
                  return_to, fallback)


def post_edit(form):
    project_id = _field(form, "project_id")
    return_to = _field(form, "return_to")
    fallback = pr_projects.project_path(project_id)
    store, refused = _loaded(return_to, fallback)
    if refused:
        return refused
    if not _field(form, "name").strip():
        return redirect_url(return_to, fallback, flash="noname")
    try:
        pr_store.edit_project(store, project_id, _field(form, "name"),
                              _field(form, "description"))
    except pr_store.StoreError:
        return redirect_url(return_to, fallback, flash="gone")
    return _saved(store, redirect_url(return_to, fallback, flash="edited"),
                  return_to, fallback)


def post_delete(form):
    fallback = pr_projects.INDEX_PATH
    store, refused = _loaded(None, fallback)
    if refused:
        return refused
    try:
        pr_store.delete_project(store, _field(form, "project_id"))
    except pr_store.StoreError:
        return redirect_url(None, fallback, flash="gone")
    return _saved(store, redirect_url(None, fallback, flash="deleted"), None, fallback)


def post_add_pr(form):
    """Add by reference: anyone's PR, any repo you can read, any state."""
    project_id = _field(form, "project_id")
    return_to = _field(form, "return_to")
    fallback = pr_projects.project_path(project_id)
    typed = _field(form, "ref")
    note = _field(form, "note")

    store, refused = _loaded(return_to, fallback)
    if refused:
        return refused

    ref = pr_projects.parse_pr_ref(typed)
    if ref is None:
        # Input is never silently discarded: the typed text round-trips so the
        # field re-renders populated.
        return redirect_url(return_to, fallback, flash="badref", text=typed)
    repo, number = ref

    try:
        found = pr_core.fetch_prs_by_ref([ref]).get(ref)
    except pr_core.PRViewerError:
        return redirect_url(return_to, fallback, flash="fetchfail", text=typed)
    if found is None:
        return redirect_url(return_to, fallback, flash="badref", text=typed)

    try:
        outcome = pr_store.add_entry(store, project_id, repo, number, note)
    except pr_store.StoreError:
        return redirect_url(return_to, fallback, flash="gone")

    anchor = pr_core.row_anchor(repo, number)
    location = redirect_url(return_to, fallback, anchor=anchor,
                            flash="added" if outcome == "added" else "exists",
                            pr=number, repo=repo, project=[project_id])
    if outcome == "exists":
        # Not an error, and the existing note is untouched — nothing to save.
        return location
    return _saved(store, location, return_to, fallback)


def post_entry_note(form):
    project_id = _field(form, "project_id")
    return_to = _field(form, "return_to")
    fallback = pr_projects.project_path(project_id)
    repo, number = _field(form, "repo"), _int_field(form, "number")
    store, refused = _loaded(return_to, fallback)
    if refused:
        return refused
    try:
        pr_store.set_note(store, project_id, repo, number, _field(form, "note"))
    except pr_store.StoreError:
        return redirect_url(return_to, fallback, flash="gone")
    anchor = pr_core.row_anchor(repo, number)
    return _saved(store,
                  redirect_url(return_to, fallback, anchor=anchor,
                               flash="noted", pr=number),
                  return_to, fallback)


def _visibility_predicate(project, show_closed):
    """`lambda e: True` when closed PRs are shown, otherwise a predicate over
    the freshly-fetched state — so the move agrees with what the page showed.

    If the fetch fails we fall back to treating everything as visible: a move
    that behaves like the unfiltered list beats a move that refuses.
    """
    if show_closed:
        return lambda _entry: True
    refs = [(e["repo"], e["number"]) for e in project["entries"]]
    try:
        prs = pr_core.fetch_prs_by_ref(refs)
    except pr_core.PRViewerError:
        return lambda _entry: True

    def visible(entry):
        pr = prs.get((entry["repo"], entry["number"]))
        # An unavailable entry always shows — Remove is its only fix.
        return pr is None or pr_core.pr_is_open(pr)

    return visible


def post_entry_move(form):
    project_id = _field(form, "project_id")
    return_to = _field(form, "return_to")
    fallback = pr_projects.project_path(project_id)
    repo, number = _field(form, "repo"), _int_field(form, "number")
    direction = _field(form, "direction")
    store, refused = _loaded(return_to, fallback)
    if refused:
        return refused

    project = pr_store.get(store, project_id)
    if project is None:
        return redirect_url(return_to, fallback, flash="gone")

    anchor = pr_core.row_anchor(repo, number)
    try:
        moved = pr_store.move_entry(
            store, project_id, repo, number, direction,
            _visibility_predicate(project, _field(form, "closed") == "show"),
        )
    except pr_store.StoreError:
        return redirect_url(return_to, fallback, flash="gone")
    if not moved:
        return redirect_url(return_to, fallback, anchor=anchor)
    return _saved(store,
                  redirect_url(return_to, fallback, anchor=anchor,
                               flash="moved", pr=number),
                  return_to, fallback)


def post_entry_remove(form):
    project_id = _field(form, "project_id")
    return_to = _field(form, "return_to")
    fallback = pr_projects.project_path(project_id)
    repo, number = _field(form, "repo"), _int_field(form, "number")
    store, refused = _loaded(return_to, fallback)
    if refused:
        return refused
    try:
        pr_store.remove_entry(store, project_id, repo, number)
    except pr_store.StoreError:
        return redirect_url(return_to, fallback, flash="gone")
    # No anchor: the row it would name is exactly what just went away.
    return _saved(store,
                  redirect_url(return_to, fallback, flash="removed", pr=number),
                  return_to, fallback)


def post_add_to_projects(form):
    """Home's `+ Project`: check some projects, optionally name a new one, add
    the PR to all of them in one action."""
    return_to = _field(form, "return_to")
    fallback = pr_projects.HOME_PATH
    repo = _field(form, "repo")
    number = _int_field(form, "number")
    note = _field(form, "note")
    store, refused = _loaded(return_to, fallback)
    if refused:
        return refused
    if number is None or not repo:
        return redirect_url(return_to, fallback, flash="error")

    targets = list(form.get("project_id") or [])
    new_name = _field(form, "new_project_name").strip()
    if new_name:
        # No duplicate-name confirmation here: §Home is explicit that prose and
        # friction mid-triage is the thing to avoid.
        targets.append(pr_store.create_project(store, new_name)["id"])
    if not targets:
        return redirect_url(return_to, fallback, flash="error")

    added, exists = [], []
    for project_id in targets:
        try:
            outcome = pr_store.add_entry(store, project_id, repo, number, note)
        except pr_store.StoreError:
            continue
        (added if outcome == "added" else exists).append(project_id)

    if not added and not exists:
        return redirect_url(return_to, fallback, flash="gone")

    # In uncategorized mode the row is about to disappear, so the form names
    # its successor; elsewhere the row itself is the right place to land.
    anchor = _field(form, "next_anchor") or pr_core.row_anchor(repo, number)
    location = redirect_url(
        return_to, fallback, anchor=anchor,
        flash="added" if added else "exists", pr=number, repo=repo,
        project=added or exists,
    )
    if not added:
        return location
    return _saved(store, location, return_to, fallback)


def post_recover(form):
    """Set a damaged store aside and start an empty one — the only write that
    happens to a store we couldn't parse, and it renames rather than replaces.

    Refuses anything that isn't actually damaged, so a newer-version store (or
    a race with someone fixing the file by hand) can't be discarded here.
    """
    fallback = pr_projects.INDEX_PATH
    if not pr_store.needs_recovery():
        return redirect_url(None, fallback)
    try:
        pr_store.move_aside()
    except OSError:
        return redirect_url(None, fallback, flash="savefail")
    return _saved(pr_store.empty_store(),
                  redirect_url(None, fallback, flash="recovered"), None, fallback)


POST_ROUTES = {
    "/project/recover": post_recover,
    "/project/create": post_create,
    "/project/edit": post_edit,
    "/project/delete": post_delete,
    "/project/add-pr": post_add_pr,
    "/project/entry/note": post_entry_note,
    "/project/entry/move": post_entry_move,
    "/project/entry/remove": post_entry_remove,
    "/pr/add-to-projects": post_add_to_projects,
}


# ---------------------------------------------------------------------------
# GET handlers
# ---------------------------------------------------------------------------

def render_index_page(user):
    """The projects index. Two GitHub fetches — noted as a real cost — and both
    degrade rather than 500: a missing count beats a missing page."""
    store, store_error = pr_store.load()

    entries_known = True
    try:
        pr_by_ref = pr_core.fetch_prs_by_ref(pr_store.all_refs(store))
    except pr_core.PRViewerError:
        pr_by_ref, entries_known = {}, False

    uncategorized = None
    if not store_error:
        try:
            _login, prs = pr_core.fetch_prs(user)
            member = pr_store.membership(store)
            uncategorized = sum(
                1 for pr in prs
                if (pr["repository"]["nameWithOwner"], pr["number"]) not in member
            )
        except pr_core.PRViewerError:
            uncategorized = None
    return store, store_error, pr_by_ref, entries_known, uncategorized


def render_detail_page(project_id, params):
    """Returns (html, status). The one GET in the app that writes: an explicit
    `?closed=` is remembered per project, and failing to remember it must never
    break the page."""
    store, store_error = pr_store.load()
    project = pr_store.get(store, project_id)
    if project is None:
        if store_error:
            return pr_projects.render_message_page(
                "PR Viewer — projects", "Projects unavailable",
                f'<p class="flash bad">{html.escape(store_error)}</p>'
            ), 500
        return pr_projects.render_not_found("project"), 404

    explicit = (params.get("closed") or [None])[0]
    if explicit in ("hide", "show"):
        show_closed = explicit == "show"
        if pr_store.set_show_closed(store, project_id, show_closed):
            try:
                pr_store.save(store)
            except pr_store.StoreError:
                pass          # a forgotten filter is not worth failing a page over
    else:
        show_closed = bool(project.get("show_closed"))

    refs = [(e["repo"], e["number"]) for e in project["entries"]]
    fetch_error = None
    try:
        pr_by_ref = pr_core.fetch_prs_by_ref(refs)
    except pr_core.PRViewerError as e:
        pr_by_ref, fetch_error = {}, str(e)

    return pr_projects.render_detail(
        store, project, pr_by_ref, show_closed, params, fetch_error
    ), 200


def _make_handler(default_user):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def _send(self, status, body, content_type="text/html; charset=utf-8"):
            encoded = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _redirect(self, location):
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        # -- GET -----------------------------------------------------------

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            params = parse_qs(parsed.query)

            if path == "/favicon.ico":
                # No favicon; 204 avoids a noisy 404 + a wasted GitHub fetch.
                self.send_response(204)
                self.end_headers()
                return

            try:
                if path == "/":
                    self._home(params)
                    return
                if path == pr_projects.INDEX_PATH:
                    user = params.get("user", [default_user])[0]
                    args = render_index_page(user)
                    self._send(200, pr_projects.render_index(*args, params))
                    return
                m = PROJECT_RE.match(path)
                if m:
                    body, status = render_detail_page(unquote(m.group(1)), params)
                    self._send(status, body)
                    return
                m = PROJECT_DELETE_RE.match(path)
                if m:
                    self._delete_page(unquote(m.group(1)))
                    return
            except pr_core.PRViewerError as e:
                self._send(500, _error_page(str(e)))
                return

            self._send(404, pr_projects.render_not_found("page"))

        def _home(self, params):
            user = params.get("user", [default_user])[0]
            try:
                login, prs = pr_core.fetch_prs(user)
            except pr_core.PRViewerError as e:
                self._send(500, _error_page(str(e)))
                return
            # An unreadable store must degrade to today's plain list, not an
            # error page — this is the page in daily use.
            store, store_error = pr_store.load()
            self._send(200, pr_projects.render_home(
                login, prs, store, store_error, params
            ))

        def _delete_page(self, project_id):
            store, _error = pr_store.load()
            project = pr_store.get(store, project_id)
            if project is None:
                self._send(404, pr_projects.render_not_found("project"))
                return
            self._send(200, pr_projects.render_delete_page(project))

        # -- POST ----------------------------------------------------------

        def do_POST(self):
            if not self._same_origin():
                self._send(403, _error_page(
                    "Cross-origin POSTs are refused. This server only accepts "
                    "form submissions from its own pages."
                ))
                return

            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length < 0:
                self._send(400, _error_page("Bad Content-Length."))
                return
            if length > MAX_BODY:
                self._send(413, _error_page("That form submission is too large."))
                return

            route = POST_ROUTES.get(urlparse(self.path).path)
            if route is None:
                self._send(404, pr_projects.render_not_found("action"))
                return

            body = self.rfile.read(length).decode("utf-8", "replace")
            form = parse_qs(body, keep_blank_values=True)
            try:
                # One lock around the whole read-modify-write. Not strictly
                # needed while HTTPServer is single-threaded, but it's the line
                # that keeps a later switch to ThreadingHTTPServer from
                # interleaving two writes into a lost note.
                with pr_store.lock():
                    location = route(form)
            except Exception as e:               # noqa: BLE001 — the server stays up
                self.log_error("POST %s failed: %s", self.path, e)
                location = redirect_url(
                    _field(form, "return_to"), pr_projects.INDEX_PATH, flash="error"
                )
            self._redirect(location)

        def _same_origin(self):
            """The server has been read-only until now; it's about to accept
            mutations, so a page on another origin must not be able to POST
            here. Modern browsers send both headers on a cross-site form post,
            which closes the drive-by case; with the loopback bind that's
            proportionate for a personal tool."""
            site = self.headers.get("Sec-Fetch-Site")
            if site is not None and site != "same-origin":
                return False
            origin = self.headers.get("Origin")
            if origin is not None:
                if urlparse(origin).netloc != (self.headers.get("Host") or ""):
                    return False
            return True

        def log_message(self, format, *args):
            # Concise one-line log instead of the default stderr spew.
            print(f"{self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

    return Handler


def serve(user, host="127.0.0.1", port=8765):
    handler = _make_handler(user)
    httpd = HTTPServer((host, port), handler)
    print(f"Serving PRs for {user} at http://{host}:{port}/  (Ctrl-C to stop)")
    print(f"Projects are stored in {pr_store.store_path()}")
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
