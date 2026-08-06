"""A filesystem cache with a short TTL for the GitHub fetches.

Pure data: no HTML, no GitHub, no HTTP. This is the only module that touches
the cache directory, so swapping JSON files for something else later is a
one-module change.

Everything here is 100% re-derivable from GitHub, which is what makes the whole
design cheap: every failure — absent, truncated, unparseable, wrong version,
stale, clock-skewed — is a cache *miss*, so the cache can never be the reason a
page breaks, and nothing in it needs backing up.

Off by default. Entry points (`pr_viewer.main`, `pr_server.main`) turn it on;
library callers and tests get today's uncached behaviour unless they opt in.

See vibe-prompts/caching/PLAN.md for the design.
"""

import hashlib
import json
import os
import time
from pathlib import Path

VERSION = 1
DEFAULT_TTL_SECONDS = 60

# Off until an entry point turns it on. A library-level default of *on* would
# mean a test's fetch could silently satisfy another test's, so a test could
# pass without the code under it ever running.
_enabled = False


def set_enabled(flag):
    global _enabled
    _enabled = bool(flag)


def enabled():
    return _enabled


def cache_dir():
    """Where the cache lives right now.

    Read at call time, not import time, so tests (and anything that sets the
    environment late) can point `PR_VIEWER_CACHE` somewhere else.
    """
    return Path(
        os.environ.get("PR_VIEWER_CACHE") or Path(__file__).with_name(".pr-cache")
    )


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
    fetched_at = entry.get("fetched_at")
    if not isinstance(fetched_at, (int, float)):
        return False, None
    age = time.time() - fetched_at
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
        tmp.write_text(
            json.dumps({"version": VERSION, "key": key, "fetched_at": time.time(),
                        "value": value}),
            encoding="utf-8",
        )
        os.replace(tmp, path)         # readers never see a half-written entry
    except (OSError, TypeError, ValueError):
        try:
            tmp.unlink()
        except OSError:
            pass


def clear():
    """Delete every entry. Returns how many files went. Never raises."""
    removed = 0
    try:
        paths = list(cache_dir().glob("*"))
    except OSError:
        return 0
    for path in paths:
        # Only our own files. `PR_VIEWER_CACHE` is environment-supplied, so
        # this must never be a recursive delete of whatever it points at.
        if path.suffix == ".json" or ".tmp-" in path.name:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed
