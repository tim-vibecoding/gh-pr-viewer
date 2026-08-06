"""Rendering for projects — the index, the project detail page, and the
additions the projects feature makes to the home page.

Everything here is HTML. Storage lives in `pr_store`, GitHub in `pr_core`,
routing in `pr_server`; this module reads from the first two and produces
strings for the third.

Every reveal-in-place control is a `<details>`/`<summary>` pair: native,
keyboard-focusable, independently toggleable, and it makes "no essential
behavior depends on JS" true absolutely rather than by convention.

See vibe-prompts/projects/PLAN.md for the design.
"""

import html
import re
from urllib.parse import quote, urlencode

import pr_core
import pr_store

HOME_PATH = "/"
INDEX_PATH = "/projects"


# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

def url(path, params=None, anchor=None):
    """Build a path + query + fragment, escaped for an href attribute."""
    pairs = [(k, v) for k, v in (params or {}).items() if v not in (None, "")]
    out = path + ("?" + urlencode(pairs) if pairs else "")
    if anchor:
        out += "#" + quote(anchor, safe="-_")
    return out


def href(path, params=None, anchor=None):
    return html.escape(url(path, params, anchor), quote=True)


def project_path(project_id):
    return f"/projects/{quote(project_id, safe='')}"


def project_anchor(project_id):
    """`project-9f3a1c22` — the id a move redirect targets, so the index lands
    scrolled to the row you just moved. Sanitized: ids come from a file a human
    can edit, and this one goes straight into a `Location` header."""
    return "project-" + re.sub(r"[^A-Za-z0-9_-]", "-", project_id)


def project_url(project_id, closed=None, anchor=None, **flash):
    return url(project_path(project_id), dict({"closed": closed}, **flash), anchor)


def home_url(filter_mode=None, user=None, anchor=None, **flash):
    return url(HOME_PATH, dict({"filter": filter_mode, "user": user}, **flash), anchor)


# ---------------------------------------------------------------------------
# PR references
# ---------------------------------------------------------------------------

# Owner and name are validated rather than accepted whole: they end up in
# GraphQL variables and in the store, and `[A-Za-z0-9._-]+` is the whole of
# what GitHub allows.
_REF_RE = re.compile(
    r"^(?:https?://(?:www\.)?github\.com/)?"
    r"(?P<owner>[A-Za-z0-9._-]+)/(?P<repo>[A-Za-z0-9._-]+)"
    r"(?:/pull/|/pulls/|#)"
    r"(?P<number>\d+)"
    r"(?:[/?#].*)?$"
)


def parse_pr_ref(text):
    """Return (repo, number) for a PR URL / `owner/repo#123` / `owner/repo/pull/123`.

    Returns None if it doesn't parse. A bare `123` is deliberately rejected:
    without a repo it's a guess, and guessing wrong files a stranger's PR.
    """
    m = _REF_RE.match((text or "").strip())
    if not m:
        return None
    return f"{m.group('owner')}/{m.group('repo')}", int(m.group("number"))


# ---------------------------------------------------------------------------
# Small rendering helpers
# ---------------------------------------------------------------------------

def _one(params, key, default=""):
    values = (params or {}).get(key) or []
    return values[0] if values else default


def _hidden(**fields):
    return "".join(
        f'<input type="hidden" name="{html.escape(k, quote=True)}" '
        f'value="{html.escape("" if v is None else str(v), quote=True)}">'
        for k, v in fields.items()
    )


def count_line(n_open, n_closed, n_unavailable=0, known=True):
    """`12 PRs · 9 open, 3 closed` — stated in both filter states, so a
    narrowed view can never be mistaken for the whole picture."""
    total = n_open + n_closed + n_unavailable
    label = f"{total} PR" if total == 1 else f"{total} PRs"
    if not known or total == 0:
        return label
    if n_closed and not n_open and not n_unavailable:
        return f"{label} · all closed"
    parts = []
    if n_open:
        parts.append(f"{n_open} open")
    if n_closed:
        parts.append(f"{n_closed} closed")
    if n_unavailable:
        parts.append(f"{n_unavailable} unavailable")
    return f"{label} · " + ", ".join(parts)


_URL_RE = re.compile(r"https?://[^\s<>\"']+")


def render_note_text(note):
    """Escaped note text with bare URLs linkified. No markdown: these are
    scratch notes, not documents. Line breaks survive via `white-space:
    pre-wrap` in the CSS."""
    out, pos = [], 0
    for m in _URL_RE.finditer(note):
        out.append(html.escape(note[pos:m.start()]))
        link = m.group(0).rstrip(".,;:)]}")
        out.append(
            f'<a href="{html.escape(link, quote=True)}">{html.escape(link)}</a>'
        )
        pos = m.start() + len(link)
    out.append(html.escape(note[pos:]))
    return "".join(out)


def _filter_control(label, options, aria_label=None):
    """A two-state filter as links, with `aria-current` on the active one."""
    aria = f' aria-label="{html.escape(aria_label, quote=True)}"' if aria_label else ""
    links = "".join(
        '<a href="{}"{}>{}</a>'.format(
            html.escape(target, quote=True),
            ' aria-current="page"' if active else "",
            html.escape(text),
        )
        for text, target, active in options
    )
    return f'<span class="filter"{aria}><span class="label">{html.escape(label)}</span>{links}</span>'


# ---------------------------------------------------------------------------
# Flash messages
# ---------------------------------------------------------------------------
#
# No session store, so the flash rides the redirect query — but as a code plus
# arguments, never as prose. The URL can't put arbitrary words in the app's own
# voice, and every argument is escaped on the way out regardless.

class _Flash:
    def __init__(self, params, store):
        self.params = params
        self.store = store

    def pr(self):
        return html.escape(_one(self.params, "pr"))

    def repo(self):
        return _one(self.params, "repo")

    def projects(self):
        ids = (self.params or {}).get("project") or []
        found = [pr_store.get(self.store, pid) for pid in ids]
        return [p for p in found if p is not None]

    def project_links(self):
        """Project names, each linking to this PR's row in that project."""
        anchor = None
        if self.repo() and self.pr():
            anchor = pr_core.row_anchor(self.repo(), _one(self.params, "pr"))
        links = [
            f'<a href="{href(project_path(p["id"]), anchor=anchor)}">'
            f'{html.escape(p["name"])}</a>'
            for p in self.projects()
        ]
        if not links:
            return ""
        if len(links) == 1:
            return links[0]
        return ", ".join(links[:-1]) + " and " + links[-1]

    def edit_note_link(self):
        """`Edit note`, into the project page — what §Home asks for after an
        add. Only when exactly one project is named; with several, the project
        links above already lead there."""
        projects = self.projects()
        if len(projects) != 1 or not self.repo():
            return ""
        anchor = "note-" + pr_core.row_anchor(self.repo(), _one(self.params, "pr"))
        return (
            f' <a href="{href(project_path(projects[0]["id"]), anchor=anchor)}">'
            "Edit note</a>"
        )


def _f_added(f):
    where = f.project_links() or "the project"
    return f"#{f.pr()} added to {where}.{f.edit_note_link()}"


FLASH = {
    "added":    (False, _f_added),
    "exists":   (False, lambda f: f"#{f.pr()} is already in {f.project_links() or 'this project'}."),
    "removed":  (False, lambda f: f"#{f.pr()} removed from the project. Its note is gone."),
    "noted":    (False, lambda f: f"Note saved for #{f.pr()}."),
    "moved":    (False, lambda f: f"Moved #{f.pr()}."),
    # Names the project by id, not by spelling it into the query: `name` is the
    # new-project form's field, and a flash must never pre-fill it.
    "pmoved":   (False, lambda f: f"Moved {f.project_links() or 'the project'}."),
    "created":  (False, lambda f: "Project created."),
    "edited":   (False, lambda f: "Project updated."),
    "deleted":  (False, lambda f: "Project deleted. The PRs themselves are untouched."),
    "badref":   (True,  lambda f: (
        "Couldn't find that PR — check the URL, or you may not have access to "
        "that repo."
    )),
    "fetchfail": (True, lambda f: (
        "Couldn't reach GitHub to check that PR, so nothing was added. Your "
        "text is still in the field."
    )),
    "dupname":  (True,  lambda f: (
        f"A project named &ldquo;{html.escape(_one(f.params, 'name'))}&rdquo; "
        "already exists — create anyway?"
    )),
    "noname":   (True,  lambda f: "A project needs a name."),
    "recovered": (False, lambda f: (
        "Started a fresh projects file. The unreadable one was kept alongside "
        "it as projects.json.corrupt-1 (or -2, -3…) — nothing was deleted."
    )),
    "gone":     (True,  lambda f: "That project no longer exists."),
    "refetched": (False, lambda f: "Cache cleared — re-fetched from GitHub."),
    "unreadable": (True, lambda f: (
        "projects.json couldn't be read, so nothing was changed. The file has "
        "been left exactly as it is."
    )),
    "savefail": (True,  lambda f: (
        "Couldn't write projects.json, so nothing was changed. Check the "
        "server log."
    )),
    "error":    (True,  lambda f: "Something went wrong; nothing was changed."),
}


def render_flash(params, store):
    code = _one(params, "flash")
    spec = FLASH.get(code)
    if spec is None:
        return ""
    bad, renderer = spec
    return f'<p class="flash{" bad" if bad else ""}">{renderer(_Flash(params, store))}</p>'


# ---------------------------------------------------------------------------
# Shared page furniture
# ---------------------------------------------------------------------------

def refresh_form(return_to=None):
    """Clear the cache and re-render, landing back on the same page.

    A POST, like every other mutation: a GET would let a link prefetch or a
    crawler clear the cache. Server-only — the CLI writes a static file, so the
    form would post nowhere.
    """
    return (
        '<form class="nav-form" method="post" action="/cache/clear">'
        + _hidden(return_to=return_to or "")
        + '<button class="btn" type="submit" '
        'title="Discard cached GitHub data and re-fetch now">'
        "&#8635; Refresh</button></form>"
    )


def _nav(*extra, return_to=None):
    # The refresh form goes first: `nav.nav a:not(:last-child)::after` paints
    # the `|` separators, so anything after the outbound links would give the
    # last one a trailing pipe.
    return refresh_form(return_to) + "".join(extra) + pr_core.HOME_NAV_LINKS


def _breadcrumbs(*crumbs):
    """(text, url_or_None) pairs; the last one is the current page."""
    parts = []
    for text, target in crumbs:
        if target is None:
            parts.append(html.escape(text))
        else:
            parts.append(f'<a href="{html.escape(target, quote=True)}">{html.escape(text)}</a>')
    return '<p class="breadcrumbs">' + " › ".join(parts) + "</p>"


def render_message_page(title, heading, body_html):
    return pr_core.page_shell(
        title,
        _breadcrumbs(("Open PRs", HOME_PATH), ("Projects", INDEX_PATH), (heading, None))
        + f"<h1>{html.escape(heading)}</h1>",
        _nav(f'<a class="internal" href="{INDEX_PATH}">Projects</a>',
             return_to=INDEX_PATH),
        body_html,
    )


def render_not_found(what="page"):
    return render_message_page(
        "PR Viewer — not found",
        "Not found",
        f'<p class="empty">No such {html.escape(what)}.</p>'
        f'<p><a href="{INDEX_PATH}">Back to projects</a></p>',
    )


# ---------------------------------------------------------------------------
# Projects index — GET /projects
# ---------------------------------------------------------------------------

def _new_project_form(params):
    """The `+ New project` disclosure. Re-opens itself, pre-filled, when a
    duplicate name needs confirming — the one place Create asks a question."""
    duplicate = _one(params, "flash") in ("dupname", "noname")
    name = html.escape(_one(params, "name"), quote=True)
    description = html.escape(_one(params, "desc"))
    confirm = _hidden(confirm="1") if _one(params, "flash") == "dupname" else ""
    return (
        f'<details class="disclosure" id="new-project"{" open" if duplicate else ""}>'
        '<summary class="btn">+ New project</summary>'
        '<div class="panel">'
        '<form method="post" action="/project/create">'
        + confirm +
        '<p><label>Name <input type="text" name="name" required '
        f'value="{name}"{" autofocus" if duplicate else ""}></label></p>'
        '<p><label>Description <span class="count-line">(optional)</span>'
        f'<textarea name="description" rows="2">{description}</textarea></label></p>'
        '<button class="btn primary" type="submit">'
        + ("Create anyway" if confirm else "Create") +
        "</button>"
        "</form></div></details>"
    )


def _recovery_form():
    """The only path that writes over a damaged store, and it never destroys
    one: the unreadable file is renamed to `projects.json.corrupt-<n>` first,
    so you can still open it in an editor and copy your notes back out.

    Offered only for a file that can't be read at all — a store from a newer
    version is readable, just not writable by us, and starting over there would
    throw away real data.
    """
    if not pr_store.needs_recovery():
        return ""
    return (
        '<details class="disclosure">'
        '<summary class="btn danger">Start a new projects file</summary>'
        '<div class="panel">'
        "<p>This keeps the unreadable file (renamed to "
        "<code>projects.json.corrupt-1</code>) and starts an empty one, so you "
        "can open the old file in an editor and copy anything out of it. "
        "Nothing is deleted.</p>"
        '<form method="post" action="/project/recover">'
        '<button class="btn danger" type="submit">Set it aside and start over</button>'
        "</form></div></details>"
    )


def render_index(store, store_error, pr_by_ref, entries_known, uncategorized, params):
    """The projects index.

    pr_by_ref     — fetched PRs for every entry across every project, or {}.
    entries_known — False if that fetch failed, so rows say `12 PRs` with no
                    open/closed split rather than a wrong one.
    uncategorized — count for the footer link, or None to omit it. Both
                    degrade rather than 500: neither is worth losing the page.
    """
    parts = [render_flash(params, store)]
    if store_error:
        parts.append(f'<p class="flash bad">{html.escape(store_error)}</p>')
        parts.append(_recovery_form())

    projects = pr_store.projects(store)
    # An unreadable or newer-version store renders read-only: a move would be
    # refused by the server anyway, and an arrow that always flashes an error
    # is worse than no arrow.
    orderable = not store_error and len(projects) > 1
    return_to = url(INDEX_PATH, {"user": _one(params, "user")})
    if not projects:
        parts.append(
            '<p class="empty">Projects are ordered lists of PRs with notes. '
            "Create one to get started.</p>"
        )
    else:
        rows = []
        for position, project in enumerate(projects):
            n_open = n_closed = n_missing = 0
            for entry in project["entries"]:
                pr = pr_by_ref.get((entry["repo"], entry["number"]))
                if pr is None:
                    n_missing += 1
                elif pr_core.pr_is_open(pr):
                    n_open += 1
                else:
                    n_closed += 1
            if entries_known:
                counts = count_line(n_open, n_closed, n_missing)
            else:
                counts = count_line(len(project["entries"]), 0, known=False)
            blurb = ""
            if project.get("description"):
                blurb = f'<div class="project-blurb">{html.escape(project["description"])}</div>'
            controls = ""
            if orderable:
                controls = move_controls(
                    "/project/move",
                    dict(project_id=project["id"], return_to=return_to),
                    f"“{project['name']}”",
                    can_up=position > 0,
                    can_down=position < len(projects) - 1,
                    css_class=" project-controls",
                )
            rows.append(
                f'<li class="project-row" id="{project_anchor(project["id"])}">'
                + controls
                + f'<a class="project-link" href="{href(project_path(project["id"]))}">'
                f'<span class="project-counts">{html.escape(counts)} ›</span>'
                f'<span class="project-name">{html.escape(project["name"])}</span>'
                f"{blurb}</a></li>"
            )
        parts.append('<ul class="projects">' + "".join(rows) + "</ul>")

    if uncategorized:
        parts.append(
            f'<p class="footer-link"><a href="{href(HOME_PATH, {"filter": "uncategorized"})}">'
            f"{uncategorized} of your open PRs aren&rsquo;t in any project →</a></p>"
        )

    heading = (
        _breadcrumbs(("Open PRs", HOME_PATH), ("Projects", None))
        + '<div class="page-head"><h1>Projects</h1>'
        f'<div class="head-actions">{_new_project_form(params)}</div></div>'
    )
    return pr_core.page_shell(
        "Projects", heading,
        _nav(f'<a class="internal" href="{HOME_PATH}">Open PRs</a>',
             return_to=INDEX_PATH),
        "\n".join(p for p in parts if p),
    )


# ---------------------------------------------------------------------------
# Project detail — GET /projects/<id>
# ---------------------------------------------------------------------------

def _stacked_hints(project, pr_by_ref):
    """{(repo, number): "#4821"} — entries whose base branch is another
    entry's head branch, within the same repo.

    The list is flat here (nesting would fight your manual order), so the stack
    relationship is stated in words instead.
    """
    heads = {}
    for entry in project["entries"]:
        pr = pr_by_ref.get((entry["repo"], entry["number"]))
        if pr:
            heads[(entry["repo"], pr["headRefName"])] = pr["number"]

    hints = {}
    for entry in project["entries"]:
        pr = pr_by_ref.get((entry["repo"], entry["number"]))
        if not pr:
            continue
        parent = heads.get((entry["repo"], pr["baseRefName"]))
        if parent is not None and parent != pr["number"]:
            hints[(entry["repo"], entry["number"])] = parent
    return hints


def move_controls(action, fields, what, can_up, can_down, css_class=""):
    """▲ ▼ ⤒ as one form with three named submit buttons. Real buttons with
    accessible labels, and a real `disabled` attribute at the ends — visibly
    disabled, not silently inert.

    Shared by the PRs inside a project and the projects on the index: same
    glyphs, same disabled ends, same one-click-one-slot promise, because they
    are the same gesture on two lists.
    """
    def button(direction, glyph, label, enabled):
        dis = "" if enabled else " disabled"
        return (
            f'<button class="btn" type="submit" name="direction" value="{direction}"'
            f' aria-label="{html.escape(label, quote=True)}" title="{html.escape(label, quote=True)}"'
            f"{dis}>{glyph}</button>"
        )

    return (
        f'<div class="entry-controls{css_class}">'
        f'<form method="post" action="{action}">'
        + _hidden(**fields)
        + button("up", "▲", f"Move {what} up", can_up)
        + button("down", "▼", f"Move {what} down", can_down)
        + button("top", "⤒", f"Move {what} to the top", can_up)
        + "</form></div>"
    )


def _entry_move_controls(project, entry, return_to, can_up, can_down, closed):
    number = entry["number"]
    return move_controls(
        "/project/entry/move",
        # `closed` travels with the move so the server can rebuild the same
        # visible list the page showed — what the UI disables and what a move
        # does can't disagree.
        dict(project_id=project["id"], repo=entry["repo"], number=number,
             return_to=return_to, closed=closed),
        f"#{number}", can_up, can_down,
    )


def _note_block(project, entry, anchor, return_to):
    number = entry["number"]
    note = entry.get("note") or ""
    edit_id = "note-" + anchor
    if note:
        shown = f'<div class="note">{render_note_text(note)}</div>'
    else:
        # A faint placeholder that is itself the edit affordance, so a
        # note-less entry doesn't look broken. Linking to the form's own id
        # opens the disclosure around it.
        shown = (
            f'<p class="note-placeholder"><a class="placeholder" href="#{edit_id}">'
            "(add a note)</a></p>"
        )

    editor = (
        f'<details class="disclosure note-edit" id="{edit_id}">'
        f'<summary class="btn">Edit note</summary>'
        '<div class="panel">'
        '<form method="post" action="/project/entry/note">'
        + _hidden(project_id=project["id"], repo=entry["repo"], number=number,
                  return_to=return_to, anchor=anchor)
        + f'<textarea name="note" rows="3" aria-label="Note for #{number}">'
        + html.escape(note)
        + "</textarea>"
        '<div class="form-row">'
        '<button class="btn primary" type="submit">Save</button>'
        '<button class="btn" type="reset">Cancel</button>'
        "</div></form></div></details>"
    )
    return shown, editor


def _remove_control(project, entry, anchor, return_to):
    """Remove confirms in place when it would discard a note; an entry with no
    note has nothing unrecoverable to lose, so it skips the confirmation."""
    number = entry["number"]
    fields = _hidden(project_id=project["id"], repo=entry["repo"], number=number,
                     return_to=return_to)
    if not (entry.get("note") or "").strip():
        return (
            '<form class="inline" method="post" action="/project/entry/remove">'
            + fields
            + f'<button class="btn danger" type="submit" '
            f'aria-label="Remove #{number} from this project">Remove</button></form>'
        )
    return (
        '<details class="disclosure">'
        f'<summary class="btn danger">Remove</summary>'
        '<div class="panel">'
        '<form method="post" action="/project/entry/remove">'
        + fields
        + f"<p>Remove #{number}? Its note will be discarded — that&rsquo;s the "
        "one thing here that GitHub can&rsquo;t give back.</p>"
        # Cancel is a link back to this same row rather than a `type="reset"`
        # button, which would do nothing in a form with no fields to reset. It
        # costs a reload, but there's no half-typed text here to lose.
        '<div class="form-row">'
        f'<button class="btn danger" type="submit">Remove #{number}</button>'
        f'<a class="btn" href="{html.escape(return_to, quote=True)}#{anchor}">Cancel</a>'
        "</div></form></div></details>"
    )


def _unavailable_row(entry):
    """An entry whose PR came back None still renders from stored data alone.
    One bad entry must never make a project unviewable."""
    number = entry["number"]
    repo = entry["repo"]
    pr_url = html.escape(f"https://github.com/{repo}/pull/{number}", quote=True)
    repo_url = html.escape(f"https://github.com/{repo}", quote=True)
    return (
        '<div class="pr-row">'
        '<span class="pr-title"><span class="status-dot is-gone" title="unavailable"></span>'
        f'<a href="{pr_url}">#{number}</a> <span class="empty">not available</span></span>'
        f'<a class="row-repo" href="{repo_url}">{html.escape(repo)}</a>'
        "</div>"
        '<div class="checks"><span class="pill unavailable" '
        'title="Deleted, private, or you lost access">unavailable</span></div>'
    )


def render_detail(store, project, pr_by_ref, show_closed, params, fetch_error=None):
    """The project page. Read-only as far as this module is concerned: it
    renders forms, `pr_server` runs them."""
    closed_param = "show" if show_closed else "hide"
    return_to = project_url(project["id"], closed=closed_param)
    hints = _stacked_hints(project, pr_by_ref)

    n_open = n_closed = n_missing = 0
    rows = []       # (entry, pr, is_visible)
    for entry in project["entries"]:
        pr = pr_by_ref.get((entry["repo"], entry["number"]))
        if pr is None:
            n_missing += 1
            visible = True          # never hide a row whose only fix is Remove
        elif pr_core.pr_is_open(pr):
            n_open += 1
            visible = True
        else:
            n_closed += 1
            visible = show_closed
        rows.append((entry, pr, visible))

    visible_rows = [r for r in rows if r[2]]
    body = [render_flash(params, store)]
    if fetch_error:
        body.append(f'<p class="flash bad">{html.escape(fetch_error)}</p>')

    # Add a PR — deliberately more permissive than home's control: anyone's PR,
    # any repo you can read, any state. Typed text round-trips on failure, so
    # input is never silently discarded.
    prefill = html.escape(_one(params, "text"), quote=True)
    body.append(
        '<form class="add-form form-row" method="post" action="/project/add-pr">'
        + _hidden(project_id=project["id"], return_to=return_to)
        + '<label for="add-ref">Add a PR</label>'
        f'<input id="add-ref" type="text" name="ref" value="{prefill}" required '
        'placeholder="paste a GitHub PR URL or owner/repo#123" size="42">'
        '<input type="text" name="note" placeholder="note…" aria-label="Note (optional)">'
        '<button class="btn primary" type="submit">Add</button>'
        "</form>"
    )

    if not project["entries"]:
        body.append(
            '<p class="empty">No PRs yet. Paste a PR URL above, or add one from '
            f'<a href="{HOME_PATH}">your open PRs</a>.</p>'
        )
    elif not visible_rows:
        show_href = href(project_path(project["id"]), {"closed": "show"})
        n = len(project["entries"])
        body.append(
            f'<p class="empty">All {n} PR{"" if n == 1 else "s"} in this project '
            f'are closed. <a href="{show_href}">Show them</a>.</p>'
        )
    else:
        items = []
        for position, (entry, pr, _visible) in enumerate(visible_rows):
            anchor = pr_core.row_anchor(entry["repo"], entry["number"])
            can_up = position > 0
            can_down = position < len(visible_rows) - 1
            if pr is None:
                row_html = _unavailable_row(entry)
                closed_cls = " is-closed"
            else:
                hint = hints.get((entry["repo"], entry["number"]))
                hint_html = None
                if hint is not None:
                    target = pr_core.row_anchor(entry["repo"], hint)
                    hint_html = f'<span class="hint">stacked on <a href="#{target}">#{hint}</a></span>'
                row_html = pr_core.render_pr_row(pr, show_repo=True, hint=hint_html)
                closed_cls = "" if pr_core.pr_is_open(pr) else " is-closed"

            note_html, editor_html = _note_block(project, entry, anchor, return_to)
            items.append(
                f'<li class="entry{closed_cls}" id="{anchor}">'
                + _entry_move_controls(project, entry, return_to, can_up,
                                       can_down, closed_param)
                + '<div class="entry-body">'
                + row_html
                + note_html
                + '<div class="entry-actions">'
                + editor_html
                + _remove_control(project, entry, anchor, return_to)
                + "</div></div></li>"
            )
        body.append('<ul class="entries">' + "".join(items) + "</ul>")

    # Header: breadcrumbs, name, description, counts, and the closed filter.
    name = html.escape(project["name"])
    edit_form = (
        '<details class="disclosure" id="edit-project">'
        '<summary class="btn">Edit</summary>'
        '<div class="panel">'
        '<form method="post" action="/project/edit">'
        + _hidden(project_id=project["id"], return_to=return_to)
        + '<p><label>Name <input type="text" name="name" required '
        f'value="{html.escape(project["name"], quote=True)}"></label></p>'
        '<p><label>Description <textarea name="description" rows="3">'
        f'{html.escape(project.get("description") or "")}</textarea></label></p>'
        '<div class="form-row">'
        '<button class="btn primary" type="submit">Save</button>'
        '<button class="btn" type="reset">Cancel</button>'
        "</div></form></div></details>"
    )
    if project.get("description"):
        description = f'<p class="project-desc">{html.escape(project["description"])}</p>'
    else:
        description = (
            '<p class="project-desc"><a class="placeholder" href="#edit-project">'
            "(add a description)</a></p>"
        )

    counts = count_line(n_open, n_closed, n_missing, known=fetch_error is None)
    base = project_path(project["id"])
    heading = (
        _breadcrumbs(("Open PRs", HOME_PATH), ("Projects", INDEX_PATH), (project["name"], None))
        + f'<div class="page-head"><h1>{name}</h1><div class="head-actions">'
        + edit_form
        + f'<a class="btn danger" href="{href(base + "/delete")}">Delete</a>'
        + "</div></div>"
        + description
    )
    # The counts and the closed filter open the content, right above the add
    # field — the nav belongs with the page header, not between the two.
    meta = (
        '<div class="meta-row">'
        + f'<span class="count-line">{html.escape(counts)}</span>'
        + _filter_control("Closed PRs:", [
            ("Hide", url(base, {"closed": "hide"}), not show_closed),
            ("Show", url(base, {"closed": "show"}), show_closed),
        ], aria_label="Closed PRs filter")
        + "</div>"
    )

    return pr_core.page_shell(
        f"{project['name']} — PR Viewer",
        heading,
        _nav(
            f'<a class="internal" href="{HOME_PATH}">Open PRs</a>',
            f'<a class="internal" href="{INDEX_PATH}">Projects</a>',
            # Back to this project, with its `?closed=` state intact.
            return_to=url(base, {"closed": _one(params, "closed")}),
        ),
        meta + "\n" + "\n".join(p for p in body if p),
    )


# ---------------------------------------------------------------------------
# Delete confirmation — GET /projects/<id>/delete
# ---------------------------------------------------------------------------

def render_delete_page(project):
    """A real page, not a `<details>`: this is the one action with no undo, and
    the counts come from the store so it states exactly what is lost."""
    n_prs = len(project["entries"])
    n_notes = sum(1 for e in project["entries"] if (e.get("note") or "").strip())
    name = html.escape(project["name"])
    detail = f"This removes {n_prs} PR{'' if n_prs == 1 else 's'}"
    if n_notes:
        detail += f" and {n_notes} note{'' if n_notes == 1 else 's'}"
    detail += " from the project. The PRs themselves are untouched."

    heading = (
        _breadcrumbs(
            ("Open PRs", HOME_PATH),
            ("Projects", INDEX_PATH),
            (project["name"], project_path(project["id"])),
            ("Delete", None),
        )
        + f"<h1>Delete &ldquo;{name}&rdquo;?</h1>"
    )
    body = (
        f"<p>{html.escape(detail)}</p>"
        '<form method="post" action="/project/delete">'
        + _hidden(project_id=project["id"])
        + '<div class="form-row">'
        f'<button class="btn danger" type="submit">Delete &ldquo;{name}&rdquo;</button>'
        f'<a class="btn" href="{href(project_path(project["id"]))}">Cancel</a>'
        "</div></form>"
    )
    return pr_core.page_shell(
        f"Delete {project['name']}?", heading,
        _nav(return_to=project_path(project["id"]) + "/delete"), body,
    )


# ---------------------------------------------------------------------------
# Home page — GET /
# ---------------------------------------------------------------------------

def _ref_of(pr):
    return pr["repository"]["nameWithOwner"], pr["number"]


def prune_uncategorized(repo_groups, membership):
    """Drop categorized PRs; promote survivors whose parent was dropped.

    Runs as a post-pass over `build_forest`, so stack detection itself is
    untouched. A promoted PR renders as a root — full `base ← branch` label —
    with a `_promoted_under` marker naming where its parent went, so the stack
    relationship stays legible without pulling already-triaged PRs back onto
    the page. Repos left with nothing drop out entirely, heading included: an
    empty repo section reads as a bug.
    """
    def visit(nodes):
        kept = []
        for pr in nodes:
            children = visit(pr["_children"])
            pr["_children"] = children
            if _ref_of(pr) in membership:
                # Dropped. Its surviving descendants move up in its place.
                for child in children:
                    child["_promoted_under"] = pr
                kept.extend(children)
            else:
                kept.append(pr)
        return kept

    out = []
    for repo, roots in repo_groups:
        survivors = visit(roots)
        if survivors:
            out.append((repo, survivors))
    return out


class HomeContext:
    """The projects feature's additions to the home page, as one object rather
    than five parameters threaded through `render_html`.

    `interactive` is False for the CLI, which writes a static file: forms would
    post nowhere and filter links would 404, so it renders chips only.
    """

    def __init__(self, store, store_error, membership, mode, params,
                 total_open, uncategorized, interactive=True, user=None):
        self.store = store
        self.store_error = store_error
        self.membership = membership
        self.mode = mode
        self.params = params
        self.total_open = total_open
        self.uncategorized = uncategorized
        # Two different questions. `server` is "is this a real server page?" —
        # it decides whether a form can post anywhere at all. `interactive` is
        # that *and* a usable store: an unreadable store degrades to today's
        # plain list (no chips, no controls, no filter, plus one honest line
        # saying why), but it shouldn't take cache control away with it.
        self.server = interactive
        self.interactive = interactive and not store_error
        self.user = user
        self.projects = [] if store_error else pr_store.projects(store)
        self._next_anchor = {}

    # -- ordering ------------------------------------------------------------

    def set_order(self, repo_groups):
        """Record the rendered order so each row's form can name its successor.

        In uncategorized mode the row you just acted on disappears, so its own
        anchor is gone; landing on the next row means a run of adds walks down
        the list instead of snapping back to the top. The renderer already
        knows the order — nothing else needs to.
        """
        order = [pr for _repo, roots in repo_groups for pr in pr_core._walk(roots)]
        anchors = [pr_core.row_anchor(*_ref_of(pr)) for pr in order]
        for i, pr in enumerate(order):
            if i + 1 < len(anchors):
                self._next_anchor[_ref_of(pr)] = anchors[i + 1]
            elif i > 0:
                self._next_anchor[_ref_of(pr)] = anchors[i - 1]

    def return_to(self):
        return home_url(self.mode if self.mode != "all" else None, self.user)

    # -- page furniture ------------------------------------------------------

    def nav_html(self):
        parts = []
        if self.interactive:
            parts.append(f'<a class="internal" href="{INDEX_PATH}">Projects</a>')
        if self.server:
            parts.append(refresh_form(self.return_to()))
        return "".join(parts)

    def banner_html(self):
        parts = []
        if self.store_error:
            parts.append(
                '<p class="flash bad">Project data is unavailable, so this page '
                "is showing PRs only. "
                f"{html.escape(self.store_error)}</p>"
            )
        parts.append(render_flash(self.params, self.store))
        return "".join(p for p in parts if p)

    def filter_html(self):
        """All / Uncategorized, with the counts on the control itself."""
        if not self.interactive:
            return ""
        return _filter_control("Show:", [
            (f"All {self.total_open}", home_url(None, self.user), self.mode == "all"),
            (f"Uncategorized {self.uncategorized}",
             home_url("uncategorized", self.user), self.mode == "uncategorized"),
        ], aria_label="Project filter")

    def ratio_html(self):
        """Stated in both filter states, so the filter never lies by omission
        and you can see there's triage to do without switching modes first."""
        if not self.interactive or not self.total_open:
            return ""
        if not self.uncategorized:
            return '<p class="count-line">Every open PR is in a project.</p>'
        return (
            f'<p class="count-line">{self.uncategorized} of your '
            f"{self.total_open} open PRs aren&rsquo;t in any project.</p>"
        )

    def empty_html(self):
        if self.mode == "uncategorized":
            # A success message, not an absence.
            return (
                '<p class="empty">Every open PR is in a project. '
                f'<a href="{href(HOME_PATH, {"user": self.user})}">Back to all PRs</a>.</p>'
            )
        return '<p class="empty">No open pull requests found.</p>'

    # -- rows ----------------------------------------------------------------

    def row_hint(self, pr):
        """`stacked on #4830 — in Q3 migration` for a row promoted by the
        filter: the stack is no longer drawable as a tree, so it says so in
        words and links to where the parent went."""
        parent = pr.get("_promoted_under")
        if parent is None:
            return None
        parent_projects = self.membership.get(_ref_of(parent)) or []
        label = f"#{parent['number']}"
        if parent_projects:
            project = parent_projects[0]
            target = href(project_path(project["id"]),
                          anchor=pr_core.row_anchor(*_ref_of(parent)))
            label = (
                f'<a href="{target}">#{parent["number"]} — in '
                f'{html.escape(project["name"])}</a>'
            )
        return f'<span class="hint">stacked on {label}</span>'

    def row_extras(self, pr):
        if self.store_error:
            return ""
        ref = _ref_of(pr)
        chips = "".join(
            f'<a class="chip" href="{href(project_path(p["id"]), anchor=pr_core.row_anchor(*ref))}">'
            f'{html.escape(p["name"])}</a>'
            for p in self.membership.get(ref) or []
        )
        if chips:
            chips = f'<span class="chips">{chips}</span>'
        if not self.interactive:
            return f'<div class="row-extras">{chips}</div>' if chips else ""
        return f'<div class="row-extras">{chips}{self._add_form(pr, ref)}</div>'

    def _add_form(self, pr, ref):
        """One compact form: a checkbox per project, a note, a new-project name.

        Creating and adding in one action, so you never have to leave your
        list, create a project, come back, and find the PR again.
        """
        member_ids = {p["id"] for p in self.membership.get(ref) or []}
        checkboxes = []
        for project in self.projects:
            checked = " checked" if project["id"] in member_ids else ""
            already = ' <span class="already">already added</span>' if checked else ""
            checkboxes.append(
                f'<label><input type="checkbox" name="project_id" '
                f'value="{html.escape(project["id"], quote=True)}"{checked}> '
                f'{html.escape(project["name"])}{already}</label>'
            )
        picker = ""
        if checkboxes:
            picker = '<div class="project-picker">' + "".join(checkboxes) + "</div>"

        number = pr["number"]
        return (
            '<details class="disclosure">'
            f'<summary class="btn" title="Add #{number} to a project">+ Project</summary>'
            '<div class="panel">'
            '<form method="post" action="/pr/add-to-projects">'
            + _hidden(repo=ref[0], number=number, return_to=self.return_to(),
                      next_anchor=self._next_anchor.get(ref, ""))
            + picker
            + '<div class="form-row">'
            f'<input type="text" name="note" placeholder="Note (optional)" '
            f'aria-label="Note for #{number}"></div>'
            '<div class="form-row">'
            '<input type="text" name="new_project_name" placeholder="New project…" '
            f'aria-label="Add #{number} to a new project"></div>'
            '<div class="form-row">'
            '<button class="btn primary" type="submit">Add</button>'
            "</div></form></div></details>"
        )


def render_home(login, prs, store, store_error, params, interactive=True):
    """The home page, with chips and (when interactive) the project controls.

    Shared by the server and the CLI so the two can't drift.
    """
    member = {} if store_error else pr_store.membership(store)
    mode = _one(params, "filter") or "all"
    if mode != "uncategorized":
        mode = "all"

    total_open = len(prs)
    uncategorized = sum(1 for pr in prs if _ref_of(pr) not in member)

    repo_groups = pr_core.build_forest(prs)
    if mode == "uncategorized" and interactive:
        repo_groups = prune_uncategorized(repo_groups, member)

    ctx = HomeContext(store, store_error, member, mode, params, total_open,
                      uncategorized, interactive, user=_one(params, "user") or None)
    ctx.set_order(repo_groups)
    return pr_core.render_html(login, repo_groups, ctx)
