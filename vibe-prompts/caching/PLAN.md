# Plan: filesystem caching with a 60s TTL

## Goal

Stop hitting the GitHub API on every render. Cache fetch results on disk with a
one-minute TTL, and put a button in the server UI that deletes the cache and
reloads the page you were on.

Today every page load shells out to `gh api graphql` — the home page once, the
projects index **twice**, a project detail page once. Navigating home →
projects → a detail page and back is five round trips through `gh` for data
that hasn't changed. With a 60s TTL that becomes one.

New module `pr_cache.py`, plus small changes at four seams. Python stdlib only.

---

## 1. Where the cache cuts in

At `pr_core.fetch_prs` and `pr_core.fetch_prs_by_ref` — the two functions that
shell out. Everything downstream (`build_forest`, the renderers) is pure and
cheap, so there's nothing to gain by caching further up, and caching further
down (raw `gh` invocations) would key off query text instead of meaning.

Two keyspaces:

| Key | Value | Written by |
|-----|-------|------------|
| `prs/<login>` | `[login, [pr_node, ...]]` | `fetch_prs` |
| `ref/<repo>#<number>` | `pr_node` or `null` | `fetch_prs_by_ref` |

`fetch_prs_by_ref` caches **per ref**, not per call. This matters: the projects
index asks for every ref across all projects, a detail page asks for one
project's subset, and `_visibility_predicate` asks again on every move. Keyed by
the whole ref list those three would never share a thing; keyed per ref, the
index warms every page behind it. A fetch then only requests the refs it
actually missed.

Misses are cached too (`null` for a deleted repo or a revoked grant), so one
dead entry in a project doesn't re-cost a round trip every render for a minute.

## 2. `pr_cache.py`

Modelled on `pr_store.py`: pure data, no HTML, no HTTP, the only module that
touches the cache directory.

```python
VERSION = 1
DEFAULT_TTL_SECONDS = 60

# Off until an entry point turns it on — see §3.
_enabled = False


def set_enabled(flag):
    global _enabled
    _enabled = bool(flag)


def cache_dir():
    """Read at call time, not import time, so tests can point it elsewhere."""
    return Path(os.environ.get("PR_VIEWER_CACHE")
                or Path(__file__).with_name(".pr-cache"))


def ttl():
    try:
        return float(os.environ.get("PR_VIEWER_CACHE_TTL") or DEFAULT_TTL_SECONDS)
    except ValueError:
        return DEFAULT_TTL_SECONDS


def _path(key):
    # Keys hold `/` and `#`; hash rather than sanitize. The key is stored in
    # the file too, so the directory stays greppable.
    return cache_dir() / (hashlib.sha256(key.encode()).hexdigest()[:16] + ".json")


def get(key):
    """(hit, value). Never raises.

    Returns a tuple because `None` is a legitimate cached value — an
    inaccessible PR — and must not read as a miss.
    """
    if not _enabled:
        return False, None
    try:
        entry = json.loads(_path(key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, None            # absent, unreadable, truncated — all misses
    if not isinstance(entry, dict) or entry.get("version") != VERSION:
        return False, None
    age = time.time() - (entry.get("fetched_at") or 0)
    # A negative age means a clock jump or a hand-edited file; treat it as
    # stale rather than as an entry that never expires.
    if not 0 <= age < ttl():
        return False, None
    return True, entry.get("value")


def put(key, value):
    """Best effort. A cache that can't be written is not an error."""
    if not _enabled:
        return
    path = _path(key)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(
            {"version": VERSION, "key": key, "fetched_at": time.time(),
             "value": value}), encoding="utf-8")
        os.replace(tmp, path)         # readers never see a half-written entry
    except (OSError, TypeError, ValueError):
        try:
            tmp.unlink()
        except OSError:
            pass


def clear():
    """Delete every entry. Returns how many files went."""
    removed = 0
    for path in cache_dir().glob("*"):
        # Only our own files. `PR_VIEWER_CACHE` is environment-supplied, so
        # this must never be a recursive delete of whatever it points at.
        if path.suffix == ".json" or ".tmp-" in path.name:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed
```

Three properties the rest of the design leans on:

- **Every failure is a miss.** Absent, truncated, unparseable, wrong version,
  stale, clock-skewed — all fall through to a real fetch. The cache can never
  be the reason a page breaks.
- **No in-memory layer.** Every hit re-reads and re-parses from disk, so each
  caller gets its own object graph. `build_forest` and `prune_uncategorized`
  mutate PR nodes in place (`_children`, `_promoted_under`); handing two
  requests the same dict would leak one render's tree into the next.
- **No lock.** Unlike `pr_store`, there's no read-modify-write here — each
  entry is written whole via `os.replace`. A clear racing a write just produces
  a miss.

Errors are **not** cached, and stale entries are **not** served when `gh` fails.
`PRViewerError` propagates exactly as it does today, and nothing is written. The
alternative — serving last-known-good on failure — would mean the page silently
shows old data while GitHub is down, which is a bigger change to the app's
honesty than caching itself. Out of scope.

Cache location: `.pr-cache/` next to the code, overridable with
`PR_VIEWER_CACHE`, gitignored — same shape as `projects.json`. Unlike the store
it is 100% re-derivable, so nothing about it needs backing up.

## 3. Caching is an entry-point policy, not a library default

`_enabled` starts **False**. `pr_viewer.main` and `pr_server.main` call
`pr_cache.set_enabled(not args.no_cache)`.

This is the detail that keeps the change safe. The existing tests patch
`subprocess.run` and call `fetch_prs` / `fetch_prs_by_ref` directly; with a
library-level default of *on*, they'd write to a cache dir beside the code and —
worse — one test's fetch would silently satisfy another's, so a test could pass
without the code under it running. Defaulting to off means **no existing test
file needs to change**, and the cache is live exactly where a person runs the
app. `test_pr_server.py` builds handlers via `_make_handler`, not `main()`, so
it's unaffected too.

Both entry points gain `--no-cache` ("bypass the cache entirely for this run") —
the escape hatch you want the first time a PR looks wrong and you can't tell
whether it's the app or the cache.

## 4. `pr_core.py` — the two fetch functions

Rename the existing bodies to `_fetch_prs_uncached` / the chunk loop stays put,
and put the caching in front. The point is that the caching is visible at one
seam per function and the fetch logic is untouched.

```python
def fetch_prs(user):
    """Return (resolved_login, [pr_node, ...]) for the given user."""
    key = "prs/" + ("@me" if user in (None, "@me") else user)
    hit, value = pr_cache.get(key)
    if hit:
        return value[0], value[1]
    login, nodes = _fetch_prs_uncached(user)
    pr_cache.put(key, [login, nodes])
    return login, nodes
```

`fetch_prs_by_ref` keeps its contract (`{(repo, number): node_or_None}`) and
gains a miss list:

```python
def fetch_prs_by_ref(refs):
    wanted = list(dict.fromkeys(refs))
    out = {ref: None for ref in wanted}

    misses = []
    for ref in wanted:
        hit, value = pr_cache.get(f"ref/{ref[0]}#{ref[1]}")
        if hit:
            out[ref] = value
        else:
            misses.append(ref)

    for start in range(0, len(misses), REF_CHUNK_SIZE):
        chunk = misses[start:start + REF_CHUNK_SIZE]
        ...                                    # unchanged query building
        for i, ref in enumerate(chunk):
            pr = (data.get(f"e{i}") or {}).get("pullRequest")
            pr_cache.put(f"ref/{ref[0]}#{ref[1]}", pr)
            if pr is not None:
                pr.setdefault("_children", [])
                out[ref] = pr

    return out
```

An all-hits call now makes **no** subprocess call at all, which is the whole
point — and note it also can't raise, so a page that only needs cached refs
survives `gh` being broken for the rest of the minute.

The caching happens inside the fetch, before `build_forest` ever sees a node, so
no derived key (`_promoted_under`, populated `_children`) can end up on disk.

## 5. The button

A POST, like every other mutation in the app, ending in `303` back to where you
were — so refresh is safe and the landing URL fully describes the view. A GET
would let a link prefetch or a crawler clear the cache.

**Route** (`pr_server.py`):

```python
def post_clear_cache(form):
    pr_cache.clear()                       # never raises; a failed unlink is a miss
    return redirect_url(_field(form, "return_to"), pr_projects.HOME_PATH,
                        flash="refetched")

POST_ROUTES["/cache/clear"] = post_clear_cache
```

The 303 lands on the same page with an empty cache, so that render re-fetches —
"delete the cache and reload" in one click. No confirmation: nothing is lost
that isn't one GitHub call away.

**Form** (`pr_projects.py`), placed in the nav so it's on every page:

```python
def refresh_form(return_to=None):
    """Clear the cache and re-render. Server-only: the CLI writes a static
    file, so the form would post nowhere."""
    return (
        '<form class="nav-form" method="post" action="/cache/clear">'
        + _hidden(return_to=return_to or "")
        + '<button class="btn" type="submit" '
        'title="Discard cached GitHub data and re-fetch now">'
        "↻ Refresh</button></form>"
    )
```

Wired in at the two nav seams:

- `pr_projects._nav(*extra, return_to=None)` — prepends `refresh_form(return_to)`.
  Callers pass their own path: `render_index` → `INDEX_PATH`, `render_detail` →
  `project_path(project["id"])`, `render_message_page` / `render_not_found` →
  their fallback. `redirect_url` already strips `FLASH_KEYS` from `return_to`,
  so a URL carrying a stale flash is safe to pass.
- `HomeContext.nav_html()` — appends `refresh_form(self.return_to())`.

One wrinkle in `HomeContext`: `self.interactive` is `False` both for the CLI
*and* when `projects.json` is unreadable. A broken store shouldn't remove cache
control, so keep the server-ness separately:

```python
self.server = interactive                       # is this a real server page?
self.interactive = interactive and not store_error   # ...with usable projects?

def nav_html(self):
    parts = []
    if self.interactive:
        parts.append(f'<a class="internal" href="{INDEX_PATH}">Projects</a>')
    if self.server:
        parts.append(refresh_form(self.return_to()))
    return "".join(parts)
```

Nav order ends up `[Projects] [↻ Refresh] [Closed PRs | Review queue | Audit
queue]`. That ordering is load-bearing for the CSS: `nav.nav a:not(:last-child)::after`
paints the `|` separators, so the form has to sit *before* the outbound links or
the last link stops being `:last-child` and grows a trailing pipe. No change to
the separator rule is needed.

**Flash** (`pr_projects.FLASH`) — `"flash"` is already in `pr_server.FLASH_KEYS`,
so nothing else changes:

```python
"refetched": (False, lambda f: "Cache cleared — re-fetched from GitHub."),
```

**CSS** (`pr_core.CSS`, next to `.nav a.internal`) — one rule so the button
reads as the same kind of control as the internal links rather than a row
button:

```css
.nav form.nav-form { display: inline-flex; margin: 0; }
.nav form.nav-form .btn { font-size: .9rem; padding: .05rem .5rem; background: var(--subtle-bg); }
```

## 6. Tests

New `test_pr_cache.py` — the only module that enables the cache, pointing
`PR_VIEWER_CACHE` at a `TemporaryDirectory` in `setUp`:

- hit inside the TTL; miss once `fetched_at` is backdated past it
- `None` round-trips as a **hit**, not a miss (the negative-caching contract)
- garbage file, wrong `version`, and a future `fetched_at` are all misses
- `put` into an unwritable dir doesn't raise
- `clear()` removes `*.json` and `*.tmp-*` and leaves an unrelated file alone
- disabled: `get` misses and `put` writes nothing

Added to `test_pr_fetch.py` (each enabling the cache explicitly for the case):

- two `fetch_prs` calls with one patched `subprocess.run` → **one** call
- `fetch_prs_by_ref` with one ref warm and one cold → the query only names the
  cold one, and both come back
- all refs warm → `subprocess.run` never called (patch it to raise)

Added to `test_pr_server.py`:

- `POST /cache/clear` → 303 to `return_to`, and the cache dir is emptied
- with no `return_to` → 303 to `/`
- cross-origin `POST /cache/clear` is refused (covered by the existing
  `_same_origin` gate, worth one assertion since this is a new route)

## 7. README

Two statements become false and have to change:

- Limitations: *"No caching; every CLI run — and every server request — hits
  the GitHub API"* → describe the 60s TTL, `.pr-cache/`, `PR_VIEWER_CACHE`,
  `PR_VIEWER_CACHE_TTL`, and `--no-cache`.
- Server section: *"Each page load re-fetches from GitHub, so **refresh =
  update**"* → a browser refresh may now serve up-to-60s-old data; **↻ Refresh**
  is what forces a re-fetch. This is a real change to a documented contract and
  the most likely thing to surprise you.
- Project layout: add `pr_cache.py` and `test_pr_cache.py`.
- `.gitignore`: add `.pr-cache/`.

---

## Files touched

- `pr_cache.py` — **new**: `cache_dir`, `ttl`, `set_enabled`, `get`, `put`, `clear`.
- `pr_core.py` — `fetch_prs` / `fetch_prs_by_ref` gain a cache layer; two CSS rules.
- `pr_server.py` — `post_clear_cache` + route; `--no-cache`; enable at startup.
- `pr_projects.py` — `refresh_form`; `_nav(return_to=)`; `HomeContext.server` +
  `nav_html`; one `FLASH` entry; `return_to` at four `_nav` call sites.
- `pr_viewer.py` — `--no-cache`; enable at startup.
- `test_pr_cache.py` — **new**. Additions to `test_pr_fetch.py`, `test_pr_server.py`.
- `README.md`, `.gitignore`.

## Open questions / assumptions

- **`@me` is per-machine, not per-account.** Two `gh` logins on one machine
  share the `prs/@me` key, so switching accounts shows the other one's PRs for
  up to a minute. Fixable by resolving the login before keying, but that costs
  the round trip the cache is there to avoid. Assuming one account per machine.
- **Staleness isn't shown.** For up to 60s the page presents old data as
  current with nothing saying so. I'd add an "as of 12s ago" line next to the
  heading, from the cache entry's `fetched_at` — it's the honest counterpart to
  introducing staleness, and it makes ↻ Refresh legible instead of mysterious.
  Left out here as beyond "basic"; recommend as an immediate follow-on.
- **60s is a guess at your rhythm.** `PR_VIEWER_CACHE_TTL` makes it a one-env-var
  experiment, and the per-ref keying means a longer TTL mostly benefits project
  pages.
- **Nothing evicts.** A ref cached once stays on disk until ↻ Refresh, even
  after the PR leaves every project — bounded by "PRs you've ever opened a
  project page for", at a few KB each. A sweep of files older than the TTL
  during `put` would fix it if the directory ever gets big.
- **The CLI has no button.** `--no-cache` covers the "I need this fresh now"
  case; a `--clear-cache` flag seemed like one flag too many for a 60s TTL.

## Verification

- `python3 -m unittest discover` — all existing tests must pass **unchanged**
  (§3 is what makes that true; if any existing test needed editing, the
  entry-point-opt-in isn't wired the way this plan describes).
- `python3 pr_server.py`, then watch the log: load `/`, reload twice inside a
  minute → the first load runs `gh`, the reloads don't. Wait 60s, reload → it
  runs again.
- Home → Projects → a detail page → back, all inside a minute: after the first
  page, no further `gh` calls.
- Click **↻ Refresh** on each of the three pages: lands back on the same page
  (same project, same `?filter=` / `?closed=` state), flash shown, `.pr-cache/`
  empty, fresh `gh` calls in the log.
- `rm -rf .pr-cache` mid-session, and `chmod 500 .pr-cache`, and
  `echo nonsense > .pr-cache/*.json` — every page still renders.
- `python3 pr_viewer.py --no-cache` twice in a row → two `gh` calls, and
  `.pr-cache/` is not created.
- Break `gh` (`PATH=/usr/bin python3 pr_server.py`) with a warm cache → pages
  still render from cache until the TTL lapses, then show today's error page.
