"""Durable storage for projects — ordered lists of PRs with notes.

Pure data: no HTML, no GitHub, no HTTP. This is the only module that touches
the store file, so swapping JSON for something else later is a one-module
change.

The store is a single JSON file next to the code (override with
`PR_VIEWER_STORE`). Notes, descriptions, and order are the only things in this
app that can't be re-derived from GitHub, so writes are atomic and a store we
failed to parse is never overwritten.

See vibe-prompts/projects/PLAN.md for the design.
"""

import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

VERSION = 1

STORE_PATH = Path(
    os.environ.get("PR_VIEWER_STORE") or Path(__file__).with_name("projects.json")
)

# Guards read-modify-write. `HTTPServer` is single-threaded today, so this
# isn't strictly needed — but it means switching to `ThreadingHTTPServer`
# can't interleave two writes into a lost note.
_LOCK = threading.Lock()


class StoreError(Exception):
    """Raised by mutating paths; callers turn it into a flash message."""
    pass


def lock():
    """The read-modify-write lock, for `with pr_store.lock():`."""
    return _LOCK


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def empty_store():
    return {"version": VERSION, "projects": []}


def store_path():
    """Where the store actually lives right now.

    Read at call time, not import time, so tests (and anything that sets the
    environment late) can point `PR_VIEWER_STORE` somewhere else.
    """
    return Path(os.environ.get("PR_VIEWER_STORE") or STORE_PATH)


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def load():
    """Return (store, error_or_None). Never raises.

    Home must degrade to today's plain list when the store is unreadable, while
    mutations must refuse loudly. Two callers, two needs, one return value that
    serves both: on error the store comes back empty *and* flagged, so a reader
    can render it and a writer can refuse.
    """
    path = store_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # No store yet is the normal first-run state, not an error.
        return empty_store(), None
    except OSError as e:
        return empty_store(), f"Couldn't read {path.name}: {e}"

    try:
        store = json.loads(raw)
    except ValueError as e:
        return empty_store(), f"{path.name} isn't valid JSON ({e}). It has not been modified."

    if not isinstance(store, dict) or not isinstance(store.get("projects"), list):
        return empty_store(), f"{path.name} isn't in the expected shape. It has not been modified."

    version = store.get("version")
    if isinstance(version, int) and version > VERSION:
        return store, (
            f"{path.name} was written by a newer version of PR Viewer "
            f"(store version {version}, this build understands {VERSION}). "
            "Showing it read-only rather than rewriting it."
        )

    for project in store["projects"]:
        project.setdefault("entries", [])
        project.setdefault("description", "")
        project.setdefault("show_closed", False)
        project.setdefault("touched_at", "")
    return store, None


def save(store):
    """Write the store atomically. Raises StoreError on failure.

    Temp file in the same directory, flush + fsync, then `os.replace` — so a
    crash mid-write leaves the previous store intact rather than a truncated
    one.
    """
    path = store_path()
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise StoreError(f"Couldn't save {path.name}: {e}")


def needs_recovery():
    """True when the store file exists but can't be read as a store at all.

    That's the only case where "start over" is the right offer. A store from a
    newer version parses fine — it's readable, just not writable by us — and
    overwriting it would throw away someone's real data.
    """
    path = store_path()
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True
    return not isinstance(store, dict) or not isinstance(store.get("projects"), list)


def move_aside():
    """Rename an unparseable store to `projects.json.corrupt-<n>`.

    Only called before a deliberate recovery write. Returns the new path, or
    None if there was nothing to move.
    """
    path = store_path()
    if not path.exists():
        return None
    n = 1
    while True:
        target = path.with_name(f"{path.name}.corrupt-{n}")
        if not target.exists():
            break
        n += 1
    os.replace(path, target)
    return target


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def projects(store):
    """Projects, most recently touched first."""
    return sorted(
        store["projects"],
        key=lambda p: (p.get("touched_at") or "", p.get("name") or ""),
        reverse=True,
    )


def get(store, project_id):
    for project in store["projects"]:
        if project["id"] == project_id:
            return project
    return None


def _require(store, project_id):
    project = get(store, project_id)
    if project is None:
        raise StoreError("That project no longer exists.")
    return project


def _touch(project):
    project["touched_at"] = _now()


def name_exists(store, name, exclude_id=None):
    target = (name or "").strip().casefold()
    return any(
        (p.get("name") or "").strip().casefold() == target and p["id"] != exclude_id
        for p in store["projects"]
    )


def create_project(store, name, description=""):
    name = (name or "").strip()
    if not name:
        raise StoreError("A project needs a name.")
    project = {
        # Names are neither unique nor stable, so URLs key off an opaque id.
        "id": secrets.token_hex(4),
        "name": name,
        "description": (description or "").strip(),
        "touched_at": _now(),
        "show_closed": False,
        "entries": [],
    }
    store["projects"].append(project)
    return project


def edit_project(store, project_id, name, description):
    """Rename / re-describe. Deliberately does not bump `touched_at`:
    editing the title of a project you aren't working on shouldn't move it up
    the index."""
    project = _require(store, project_id)
    name = (name or "").strip()
    if not name:
        raise StoreError("A project needs a name.")
    project["name"] = name
    project["description"] = (description or "").strip()
    return project


def delete_project(store, project_id):
    project = _require(store, project_id)
    store["projects"].remove(project)
    return project


def set_show_closed(store, project_id, show_closed):
    """Remember the per-project closed filter. Not a `touch` — looking is not
    working."""
    project = _require(store, project_id)
    changed = bool(project.get("show_closed")) != bool(show_closed)
    project["show_closed"] = bool(show_closed)
    return changed


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------

def _index_of(entries, repo, number):
    for i, entry in enumerate(entries):
        if entry["repo"] == repo and entry["number"] == number:
            return i
    return -1


def add_entry(store, project_id, repo, number, note=""):
    """Return "added" or "exists". Adding a PR already present is not an error
    and never overwrites the existing note."""
    project = _require(store, project_id)
    if _index_of(project["entries"], repo, number) >= 0:
        return "exists"
    project["entries"].append(
        {"repo": repo, "number": int(number), "note": (note or "").strip()}
    )
    _touch(project)
    return "added"


def remove_entry(store, project_id, repo, number):
    project = _require(store, project_id)
    i = _index_of(project["entries"], repo, number)
    if i < 0:
        raise StoreError(f"#{number} isn't in this project.")
    entry = project["entries"].pop(i)
    _touch(project)
    return entry


def set_note(store, project_id, repo, number, note):
    project = _require(store, project_id)
    i = _index_of(project["entries"], repo, number)
    if i < 0:
        raise StoreError(f"#{number} isn't in this project.")
    project["entries"][i]["note"] = (note or "").strip()
    _touch(project)
    return project["entries"][i]


def move_entry(store, project_id, repo, number, direction, is_visible=None):
    """Move an entry one visible slot up/down, or to the top. Returns True if
    anything moved.

    Order is absolute (`UI.md` §Reordering): moving a PR while closed ones are
    hidden moves it past the hidden ones too. So the neighbour we swap with is
    the next *visible* one, and one click always produces one visible move —
    never a no-op that silently reshuffles hidden entries.
    """
    if direction not in ("up", "down", "top"):
        raise StoreError(f"Unknown direction {direction!r}.")
    if is_visible is None:
        def is_visible(_entry):
            return True

    project = _require(store, project_id)
    entries = project["entries"]
    i = _index_of(entries, repo, number)
    if i < 0:
        raise StoreError(f"#{number} isn't in this project.")

    # The moving entry counts as visible even if it isn't, so a crafted request
    # for a hidden row still does something sane rather than blowing up.
    visible = [j for j, e in enumerate(entries) if j == i or is_visible(e)]
    k = visible.index(i)

    # Each visible entry plus the hidden entries that trail it forms a block,
    # and a move swaps whole blocks. That gives the two properties the UI
    # promises at once: one click is always exactly one visible move (the
    # mover jumps the hidden ones), and up/down are exact inverses, so toggling
    # the closed filter back on can't reveal a surprise reshuffle. Hidden
    # entries before the first visible one belong to no block and stay put.
    prefix = entries[: visible[0]]
    bounds = visible + [len(entries)]
    blocks = [entries[bounds[p]:bounds[p + 1]] for p in range(len(visible))]

    target = 0 if direction == "top" else k - 1 if direction == "up" else k + 1
    if target == k or not 0 <= target < len(blocks):
        return False                      # at the end of the list; arrow disabled

    blocks.insert(target, blocks.pop(k))
    entries[:] = prefix + [e for block in blocks for e in block]
    _touch(project)
    return True


# ---------------------------------------------------------------------------
# Cross-cutting views
# ---------------------------------------------------------------------------

def membership(store):
    """{(repo, number): [project, ...]} — which projects each PR is in."""
    out = {}
    for project in store["projects"]:
        for entry in project["entries"]:
            out.setdefault((entry["repo"], entry["number"]), []).append(project)
    return out


def all_refs(store):
    """Every (repo, number) across every project, deduped, in a stable order."""
    seen = []
    known = set()
    for project in store["projects"]:
        for entry in project["entries"]:
            ref = (entry["repo"], entry["number"])
            if ref not in known:
                known.add(ref)
                seen.append(ref)
    return seen
