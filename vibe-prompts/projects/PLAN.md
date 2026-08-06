# Plan: projects — ordered lists of PRs with notes

Implements `PROMPT.md` as specified by `UI.md`. That document pins down every
behavior; this one decides how the code gets there. Where the two disagree,
`UI.md` wins — except at the handful of points called out under
**Deviations from UI.md**, which are places the spec's ideal costs more than
it's worth in a stdlib-only, no-JS app.

Stdlib only, no new dependencies — that's the project's whole ethos.

## What has to change, and why

`UI.md` §"Implications worth stating up front" names five. Mapped onto the code
as it stands today:

| Implication | Where it lands |
| --- | --- |
| Membership must be stored durably | new `pr_store.py` + a gitignored `projects.json` |
| Home reads that store on every render | `render_html` gains a membership map |
| Project pages can't use the open-PR fetch | new `fetch_prs_by_ref` (batched, any state, any author) |
| The server must accept mutations | `pr_server.py` gains routing + `do_POST` + POST/redirect/GET |
| Notes/order/descriptions are unrecoverable | atomic writes; never overwrite a store we failed to parse |

Everything else is rendering.

## Module layout

```
pr_store.py     NEW — load/save + project & entry CRUD. Pure data: no HTML,
                no GitHub, no HTTP. The only module that touches the store.
pr_core.py      + `state` in PR_FRAGMENT, + fetch_prs_by_ref, + page_shell,
                + render_pr_row (extracted), + membership plumbing on home,
                + CSS for the new surfaces.
pr_projects.py  NEW — renders the projects index and project detail pages,
                and the home-page additions (chips, "+ Project" disclosure).
                Imports shared rendering from pr_core.
pr_server.py    + routing table, do_POST, form parsing, flash redirects.
pr_viewer.py    unchanged code; renders chips but no controls (see Stage 6).
```

All store access goes through `pr_store`'s functions, never through the dict it
returns. That's the seam that makes the JSON→SQLite question a one-module change
if it ever comes up.

---

## Stage 0 — Storage

### The choice: a single JSON file

The prompt allows anything up to a gitignored SQLite database. JSON is the right
end of that range: one user, one process, data measured in kilobytes, and no
query that isn't "load the whole thing". A file you can open in an editor, diff,
and copy somewhere as a backup is worth more here than indexes we'd never use.
JSON over YAML because `json` is stdlib and `yaml` is not.

`projects.json`, next to the code:

```python
STORE_PATH = Path(os.environ.get("PR_VIEWER_STORE") or Path(__file__).with_name("projects.json"))
```

Keyed off `__file__`, not the working directory — the LaunchAgent's cwd is not
guaranteed to be the repo, and a store that silently relocates is a store that
silently loses your notes.

### Schema

```json
{
  "version": 1,
  "projects": [
    {
      "id": "9f3a1c22",
      "name": "Q3 migration",
      "description": "Splitting the monolith settings loader apart.",
      "touched_at": "2026-08-06T17:04:11Z",
      "show_closed": false,
      "entries": [
        {"repo": "khan/webapp", "number": 4821, "note": "Has to land first."}
      ]
    }
  ]
}
```

- **`id`** — `secrets.token_hex(4)`. Names are not unique and are renameable, so
  URLs key off an opaque id.
- **Order is list order.** `entries` *is* the ordering, and so is `projects` —
  both lists are manually arranged, and there's no rank column to drift out of
  sync with either.
- **`touched_at`** — bumped on add, remove, reorder, and note edit. Records when
  a project was last worked in. Not bumped by rename/describe, or by moving the
  project up the index: neither is work on its contents.
- **`ordered`** — set once a store's project order is the manual one. A store
  written before manual ordering existed is sorted by `touched_at` on load (the
  order it used to display) and flagged from then on, so the first arranged view
  is the view you last saw and nothing re-sorts behind you afterwards.
- **`show_closed`** — the remembered per-project filter state. Written whenever
  a request carries an explicit `?closed=`, so it records what you last chose.
- **`version`** — a store whose version is greater than we understand renders
  read-only with a banner rather than being rewritten into a shape the newer
  code can't read.

### The store module

```python
def load():        -> (store_dict, error_or_None)   # never raises
def save(store):   -> None                          # atomic; raises on failure
def projects(store) / get(store, project_id)
def create_project(store, name, description) -> project
def edit_project(store, project_id, name, description)
def delete_project(store, project_id) -> project     # returns what was removed
def move_project(store, project_id, direction) -> bool
def add_entry(store, project_id, repo, number, note) -> "added" | "exists"
def remove_entry(store, project_id, repo, number) -> entry
def set_note(store, project_id, repo, number, note)
def move_entry(store, project_id, repo, number, direction, is_visible) -> bool
def membership(store) -> {(repo, number): [project, ...]}
```

`load` returns an error instead of raising because home must degrade to today's
plain list when the store is unreadable, while mutations must refuse loudly.
Two callers, two needs, one return value that serves both.

**Atomic writes.** Write to a temp file in the same directory, `flush` +
`os.fsync`, then `os.replace` — so a crash mid-write leaves the previous store
intact rather than a truncated one. This is the "should survive a crash
mid-action" requirement, and it's four lines.

**Never overwrite a store we couldn't parse.** If `load` failed to parse, every
mutating path returns an error and renders it. Before any recovery write, move
the bad file aside to `projects.json.corrupt-<n>`. The one thing in this feature
that can't be re-derived from GitHub is the one thing we refuse to clobber.

**A `threading.Lock` around read-modify-write.** `HTTPServer` is single-threaded
today so it isn't strictly needed — but the lock is one line and it means
switching to `ThreadingHTTPServer` later can't interleave two writes into a lost
note.

### Reordering with hidden rows

`UI.md` §Reordering: order is absolute, and moving a PR while closed ones are
hidden "moves it past the hidden ones too". So one click always produces one
visible move — never a no-op that silently reshuffles hidden entries:

```python
def move_entry(store, project_id, repo, number, direction, is_visible):
    entries = get(store, project_id)["entries"]
    i = _index_of(entries, repo, number)
    visible = [j for j, e in enumerate(entries) if j == i or is_visible(e)]
    k = visible.index(i)
    if direction == "top":
        target = 0
    elif direction == "up":
        if k == 0:
            return False                  # already first; arrow was disabled
        target = visible[k - 1]
    else:
        if k == len(visible) - 1:
            return False
        target = visible[k + 1]
    # Both directions reduce to the same insert: for "up" the target index is
    # unaffected by the pop; for "down" the pop shifts the neighbour to
    # target-1, so inserting at `target` lands just after it.
    entries.insert(target, entries.pop(i))
    _touch(store, project_id)
    return True
```

The caller passes `is_visible` — `lambda e: True` when closed PRs are shown,
otherwise a predicate over the freshly-fetched state. Arrow disabled-ness is
computed from the same visible list, so what the UI shows and what the move does
can't disagree.

### Gitignore

There is no `.gitignore` today. Add one:

```
projects.json
projects.json.corrupt-*
pr_server.log
__pycache__/
```

`pr_server.log` is currently tracked-and-deleted in the working tree, so also
`git rm --cached pr_server.log`.

**Tests** (`test_pr_store.py`, stdlib `unittest` + `tempfile`): create/edit/
delete; add is idempotent and never overwrites a note; move up/down/top
including with hidden entries interleaved; projects move the same way and a
pre-ordering store adopts its old `touched_at` sort exactly once; a corrupt file
loads as an error and is not overwritten; an atomic write leaves no partial file.

---

## Stage 1 — Fetch PRs by reference

`UI.md` §Implication 3: the project page needs any PR, in any state, by anyone.

Add `state` to `PR_FRAGMENT` (`OPEN` / `CLOSED` / `MERGED`). Harmless for the
existing home query, and it's what both the closed filter and the merged pill
key off.

One request for a whole project, via aliases:

```graphql
query($o0:String!,$n0:String!,$p0:Int!, $o1:String!,$n1:String!,$p1:Int!) {
  e0: repository(owner:$o0, name:$n0) { pullRequest(number:$p0) { ...prFields } }
  e1: repository(owner:$o1, name:$n1) { pullRequest(number:$p1) { ...prFields } }
}
```

```python
def fetch_prs_by_ref(refs):
    """refs: [(repo, number), ...] -> {(repo, number): pr_node_or_None}.

    Raises PRViewerError only when the whole request fails. A single missing or
    inaccessible PR comes back as None so one bad entry can't take down a page.
    """
```

Three things this must get right:

- **Variables, not string interpolation.** Repo names come from user input; they
  do not get concatenated into a query. `gh api graphql -f o0=... -F p0=123`
  (`-F` types the number as an int).
- **Partial failure is normal.** A deleted repo or a lost grant returns
  `data.e3 = null` *plus* an `errors` array. `fetch_prs` raises on any `errors`
  payload — correct there, wrong here. `fetch_prs_by_ref` inspects each alias
  and raises only if `data` is entirely absent.
- **Chunk at 100 aliases** per request against GraphQL node limits, dedup
  `(repo, number)` first, and preserve nothing about order — the caller owns
  that.

**Tests:** parse a fixture payload with one null alias plus a matching `errors`
entry and assert the other entries survive; assert chunking of 150 refs issues
two calls (with `subprocess.run` patched).

---

## Stage 2 — Extract the shared PR row (pure refactor)

`UI.md` §Rows wants project rows to *be* home rows, so a status means the same
thing on both pages. One renderer, or they drift.

`render_pr` today does row markup, children recursion, and the `<li>` wrapper in
one function. Split off the middle:

```python
def render_pr_row(pr, *, is_root=True, show_repo=False, hint=None):
    """Dot, title, draft badge, branches-or-hint, and the pill line.

    show_repo — append `owner/repo` (project pages have no repo heading).
    hint      — replaces the branch label with e.g. `stacked on #4821`.
    Closed/merged PRs get a `merged`/`closed` pill and drop their check pills:
    checks on a landed PR are noise.
    """

def render_pr(pr, is_root=True, ctx=None):
    """Home's <li>: the shared row, plus chips / "+ Project", plus children."""
```

`render_pr` keeps its name and default arguments, so the existing tests in
`test_pr_core.py` — which call `c.render_pr(pr, is_root=True)` — keep passing
unchanged. That's the signal that this stage changed nothing.

Also extract the page shell, since there are about to be three pages:

```python
def page_shell(title, heading, nav_html, body_html) -> str   # <head>, CSS, copy script
```

Run the existing tests. No new ones; nothing new is true yet.

---

## Stage 3 — The project pages (read-only)

`pr_projects.py`. Render only — no mutation plumbing yet, so the data model and
the layout can be checked against `UI.md` before any button works.

### Projects index — `GET /projects`

Per `UI.md` §2: name, `N PRs · X open, Y closed`, description truncated to one
line via CSS (`text-overflow: ellipsis`, not a Python slice — the browser knows
the width), whole row a link, most-recently-touched first.

The counts need the state of every entry, so the page batch-fetches all entries
across all projects (deduped) in one `fetch_prs_by_ref`. The uncategorized
footer count additionally needs the user's open PRs. Two fetches on this page —
noted as a real cost. Both degrade rather than error: if the entry fetch fails
the row shows `12 PRs` with no open/closed split; if the open-PR fetch fails the
footer line is omitted. Neither is worth a 500.

The footer line is hidden when the count is zero, and links to
`/?filter=uncategorized`.

### Project detail — `GET /projects/<id>?closed=hide|show`

1. `load()` the store, `get()` the project → 404 page if unknown.
2. `fetch_prs_by_ref` for its entries.
3. Resolve the filter: explicit `?closed=` wins, else stored `show_closed`,
   else `hide`. An explicit value is persisted (§"remembered per project…with
   the URL winning") — which makes this GET a writer, the one in the app. It
   writes a single boolean and tolerates failure silently, because failing to
   remember a filter must never break the page.
4. Header: breadcrumbs, name, description (full, or the `(add a description)`
   placeholder), the `12 PRs · 9 open, 3 closed` count line — stated in **both**
   filter states — and the Hide/Show control.
5. Rows, flat, in stored order, filtered. Each: `render_pr_row(pr,
   show_repo=True, hint=stacked_hint)`, then the note block, then controls.

**The stacked hint.** Build `{headRefName: number}` from the project's own
entries per repo; an entry whose `baseRefName` is in that map renders
`stacked on #4821` instead of a branch label. The relationship stays legible
without nesting fighting your manual order.

**An entry whose PR came back `None`** renders from stored data alone — number,
repo, note — with an `unavailable` pill and a working Remove. One bad entry must
not make a project unviewable.

**Empty states**, per `UI.md` §"Empty and error states": no entries → "No PRs
yet…" with a link home; all entries filtered out → "All 3 PRs in this project
are closed." with a Show link. Never a bare empty list.

### Disclosures without JavaScript

Every reveal-in-place in `UI.md` — "+ Project", "+ New project", Edit note, Edit
name/description, the Remove confirmation — is a `<details>`/`<summary>` pair.
Native, keyboard-focusable, independently toggleable (several notes open at once
falls out for free), and it satisfies "no essential behavior depends on JS"
absolutely rather than by convention. CSS hides the collapsed note text when the
editor is open so it reads as the swap the spec describes.

**Anchors.** Every row carries `id="pr-<repo with / → ->-<number>"`. That's what
every redirect targets, and it's how "lands scrolled to that row" works with no
scroll-restoration code at all.

**Tests:** stacked-hint detection; closed entries render a `merged` pill and no
check pills; an `unavailable` row renders with Remove; both empty states.

---

## Stage 4 — Mutations

### Routing

`do_GET` becomes a small table instead of an `if parsed.path != "/"`:

```
GET   /                          home        ?user= &filter=all|uncategorized
GET   /projects                  index
GET   /projects/<id>             detail      ?closed=hide|show
GET   /projects/<id>/delete      delete confirmation page
POST  /project/create            name, description, confirm
POST  /project/edit              project_id, name, description
POST  /project/delete            project_id
POST  /project/add-pr            project_id, ref, note
POST  /project/entry/note        project_id, repo, number, note
POST  /project/entry/move        project_id, repo, number, direction, closed
POST  /project/entry/remove      project_id, repo, number
POST  /pr/add-to-projects        repo, number, project_ids[], new_project_name, note
```

Only `/projects/<id>` has a path parameter; everything a form submits travels in
the body. `owner/repo` contains a slash, and putting it in a path buys nothing
but escaping bugs.

### POST → 303 → GET, always

Every mutating handler ends in `303 See Other` with a `Location`. This is what
makes `UI.md` §"Refresh is always safe" true: there is no POST result page to
re-submit, back/forward behaves, and each action lands on a URL that fully
describes the view. Every form carries a hidden `return_to` (path + query) so
the redirect restores the page, filter, and anchor you acted from.

### Flash messages

No session store, so the flash rides the redirect query — but as a **code plus
arguments**, not prose:

```
/projects/9f3a1c22?closed=hide&flash=added&pr=4821#pr-khan-webapp-4821
```

`FLASH = {"added": lambda a: f"#{a['pr']} added to {a['project_name']}.", ...}`
renders it server-side. The URL never carries display text, so no one can craft
a link that puts arbitrary words in the app's own voice — and every argument is
`html.escape`d on the way out regardless.

`added` renders with the **Edit note** link into the project page that `UI.md`
§Home asks for.

### Form handling

`do_POST` reads `Content-Length` (capped, e.g. 64 KiB → `413`) and parses
`application/x-www-form-urlencoded` with `urllib.parse.parse_qs`. Unknown route
→ 404. Any `pr_store` error → the redirect carries a `flash=error` code and the
target page renders it; the server keeps running.

**One new risk worth naming.** The server has been read-only until now; it's
about to accept mutations, which means any page in your browser could POST to
`127.0.0.1:8765` and edit your projects. Proportionate mitigation, no tokens or
sessions: reject a POST whose `Sec-Fetch-Site` header is present and not
`same-origin`, and whose `Origin` is present and not our own. Modern browsers
send both on cross-site form posts, so this closes the drive-by case; combined
with the loopback bind it's enough for a personal tool.

### Adding a PR by reference

`parse_pr_ref(text)` accepts, per `UI.md` §"Adding a PR from this page":

- `https://github.com/owner/repo/pull/123` (trailing path/query/fragment ok)
- `owner/repo#123`
- `owner/repo/pull/123`

Owner and name are validated against `[A-Za-z0-9._-]+`. A bare `123` is
rejected: without a repo it's a guess, and guessing wrong files a stranger's PR.
Unparseable input, or a `fetch_prs_by_ref` miss, redirects back with
`flash=badref` **and the typed text preserved** in the query so the field
re-renders populated — "input is never silently discarded" is the requirement,
and it's the only reason that text round-trips.

Already-present → `flash=exists`, anchored to the existing row, note untouched.
Not an error.

### Delete

`GET /projects/<id>/delete` is a real page stating exactly what is lost
("…removes 12 PRs and 9 notes… The PRs themselves are untouched"), with counts
computed from the store. `POST /project/delete` then redirects to the index with
a confirmation. A page, not a `<details>` — this is the one action with no undo.

**Tests** (`test_pr_server.py`): an `HTTPServer` on port 0 with
`fetch_prs`/`fetch_prs_by_ref` patched. Assert each POST returns 303 with the
expected `Location` and mutates the temp store as expected; assert a
cross-origin POST is rejected; assert a store error yields a redirect with
`flash=error` and a live server. POST/redirect/flash is where this kind of code
breaks, so it gets the test.

---

## Stage 5 — Home page

Last, deliberately: this is the page in daily use, and by now everything it
depends on is built and tested.

`render_html` gains the membership map, threaded through as a small context
object (projects list, membership map, filter mode, `interactive` flag) rather
than five parameters.

- **Chips.** Each row shows one chip per project it belongs to, linking to that
  project.
- **"+ Project".** A `<details>` per row containing one form: a checkbox per
  project (pre-checked and labelled "already added" where the PR is already in
  it), a one-line Note, a **New project…** name field, and Add.
- **Nav.** An internal **Projects** link, styled distinctly from the outbound
  github.com links so it doesn't read as leaving the app.
- **Degradation.** If `load()` returned an error: no chips, no "+ Project", no
  filter control, plus one muted line saying the store couldn't be read.
  Today's page, honestly labelled — not a 500, and not a silent omission that
  would read as "you have no projects".

### The uncategorized filter

`?filter=uncategorized`. Default `all`. Both states show the ratio line
("5 of your 12 open PRs aren't in any project.").

Pruning runs as a post-pass over `build_forest`, so stack detection is unchanged:

```python
def prune_uncategorized(repo_groups, membership):
    """Drop categorized PRs; promote survivors whose parent was dropped.

    A promoted PR renders as a root — full `base ← branch` label — with a hint
    naming where its parent went: `stacked on #4830 — in Q3 migration`, linked.
    Repos left with nothing drop out entirely, heading included: an empty repo
    section reads as a bug.
    """
```

**Scroll after a triage add.** In uncategorized mode the row you just acted on
disappears, so its anchor is gone. Rather than tracking anything server-side,
each "+ Project" form carries a hidden `next_anchor` — the anchor of the
following row in the order *as rendered*, or the preceding row if it was last.
The redirect targets that, so a run of adds walks down the list instead of
snapping back to the top. The renderer already knows the order; nothing else
needs to.

The confirmation goes at the **top of the list**, not on the row — the row is
gone. Empty state is a success: "Every open PR is in a project." with a link
back to All.

**Tests:** pruning drops categorized PRs and empty repo groups; a survivor whose
parent was pruned is promoted, renders a root branch label, and its hint names
the parent's project; `next_anchor` points at the successor row and at the
predecessor for the last row.

---

## Stage 6 — CLI, accessibility, README

**The CLI.** `pr_viewer.py` writes a static file: forms would post nowhere and
filter links would 404. It renders **chips** (read-only, genuinely useful) and
omits every control, via the `interactive=False` flag already threaded through
in Stage 5. A `--no-projects` escape hatch keeps the pre-feature output
available. `UI.md` doesn't cover the CLI; this is the reading that leaves it
useful without lying about what it can do.

**Accessibility sweep**, per `UI.md` §Cross-cutting: reorder arrows are
`<button>`s with `aria-label="Move #4821 up"` and a real `disabled` attribute at
the ends (visibly disabled, not silently inert); filter controls are links with
`aria-current` on the active one; `<summary>` elements carry descriptive text,
not bare glyphs; tab order follows reading order. Check both color schemes.

**README:** the feature in "What it does"; a **Projects** section covering the
three pages and the two filters; where `projects.json` lives, that it's
gitignored, that it's yours to back up, and `PR_VIEWER_STORE`; the project
layout tree; and under Limitations — the projects index issues two GitHub
fetches, and the CLI renders chips but no controls.

---

## Deviations from UI.md

Stated rather than quietly absorbed:

1. **Cancel leaves the editor open.** `UI.md` §Notes wants Cancel to restore the
   note in place. Cancel is `<button type="reset">` — a real keyboard-focusable
   button that restores the stored text with no JS and no reload — but it can't
   collapse the `<details>`; the summary toggle does that. The alternatives were
   a CSS `<label>` hack (not keyboard-focusable, which breaks a firmer promise)
   or a link that reloads (refetches GitHub and closes every other open editor).
2. **No duplicate-name warning from home's "+ Project".** The index's Create
   warns and offers "create anyway?" as specified. Home's inline
   **New project…** just creates: the confirm round-trip would have to preserve
   the note field through an extra hop, and §Home is explicit that prose and
   friction mid-triage is the thing to avoid.
3. **`GET /projects/<id>` writes.** Remembering `show_closed` per project makes
   one GET a writer. It writes a single boolean, and swallows any failure —
   nothing user-authored is at risk, and the alternative (a POST to change a
   filter) breaks bookmarking and the shared-link promise.

## Verification

1. **Nothing regressed:** `python3 -m unittest discover` green, including the
   pre-existing `test_pr_core.py` untouched. `python3 pr_viewer.py --no-open`
   still writes a valid page.
2. **Create → add → reorder → note → filter,** by hand: create a project from
   the index; add a PR from home's "+ Project"; add someone else's merged PR by
   URL from the project page; walk it up and down with the arrows; Move to top;
   edit two notes at once and save each; toggle Hide/Show and confirm order is
   unchanged; confirm the count line states the hidden ones in both states.
3. **Refresh and history:** after every mutation, F5 re-renders (no re-post
   prompt), the URL describes the view, and back/forward behave.
4. **Triage to empty:** work `?filter=uncategorized` down to nothing; confirm
   each add lands on the successor row and the final state is the success
   message.
5. **Degradation:** corrupt `projects.json` by hand → home still lists PRs with
   a warning and no chips; mutations refuse; the file is not overwritten. Point
   an entry at a nonexistent repo → the row renders `unavailable` with a working
   Remove and the rest of the project is fine. `mv projects.json` away → empty
   states everywhere, no crash.
6. **Data safety:** kill the server mid-write in a loop → the store is always
   parseable and never truncated.
7. **Cross-origin POST** from a page on another origin is rejected.

## Out of scope

Everything under `UI.md` §"Out of scope" — sharing/sync, auto-population from a
query or label, nested projects, per-entry statuses, due dates, auto-archiving,
any "what changed since you last looked" signal, markdown export.

Plus, from this plan's own choices:

- **SQLite.** The prompt's upper bound; JSON is enough at this size, and
  `pr_store`'s API is the seam if that ever stops being true.
- **Drag-and-drop reordering.** `UI.md` §Reordering explicitly allows it as a
  later layer on top of the arrows — never as the only way to reorder.
- **Pagination past 100 open PRs** on home (unchanged from today).
- **Caching.** Every request still refetches. The projects index issuing two
  fetches is the first place that might start to sting; a short TTL cache is the
  answer if it does.
