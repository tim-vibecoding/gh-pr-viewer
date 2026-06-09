"""PR Viewer core — fetch a GitHub user's open PRs, group them into
stacks/trees, and render a self-contained HTML page.

This module holds everything except the CLI/server glue. Both `pr_viewer.py`
(one-shot CLI) and `pr_server.py` (local HTTP server) import from it.

See vibe-prompts/initial-creation/PLAN.md for the original design and
vibe-prompts/server/PLAN.md for the CLI/server split.
"""

import html
import json
import subprocess
from collections import defaultdict

REQUIRE_REVIEW_CHECKS = ("Require Review or Audit Label", "Review Required")
E2E_SUBSTRINGS = ("E2E Tests", "E2E Setup")


class PRViewerError(Exception):
    """Raised on any fetch/GraphQL failure; callers decide how to present it."""
    pass


# When --user is omitted we resolve the authenticated user via `viewer`.
QUERY_VIEWER = """
query {
  viewer {
    login
    pullRequests(states: OPEN, first: 100,
                 orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes { ...prFields }
    }
  }
}
""" + "\n"

QUERY_USER = """
query($login: String!) {
  user(login: $login) {
    login
    pullRequests(states: OPEN, first: 100,
                 orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes { ...prFields }
    }
  }
}
""" + "\n"

PR_FRAGMENT = """
fragment prFields on PullRequest {
  number
  title
  url
  isDraft
  baseRefName
  headRefName
  repository { nameWithOwner defaultBranchRef { name } }
  reviewDecision
  latestReviews(last: 20) {
    nodes { author { login } state submittedAt }
  }
  reviewRequests(last: 20) {
    nodes { requestedReviewer { ... on User { login } } }
  }
  statusCheckRollup {
    state
    contexts(last: 100) {
      nodes {
        __typename
        ... on CheckRun { name conclusion status detailsUrl startedAt completedAt }
        ... on StatusContext { context state targetUrl createdAt }
      }
    }
  }
}
"""


def fetch_prs(user):
    """Return (resolved_login, [pr_node, ...]) for the given user.

    user is either a login string or None (meaning the authenticated user).
    """
    if user is None or user == "@me":
        query = QUERY_VIEWER + PR_FRAGMENT
        cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
        container_key = "viewer"
    else:
        query = QUERY_USER + PR_FRAGMENT
        cmd = ["gh", "api", "graphql", "-f", f"login={user}", "-f", f"query={query}"]
        container_key = "user"

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        raise PRViewerError("`gh` CLI not found on PATH. Install it from https://cli.github.com/")
    except subprocess.CalledProcessError as e:
        raise PRViewerError(f"gh api graphql failed:\n{e.stderr.strip()}")

    payload = json.loads(result.stdout)
    if payload.get("errors"):
        msgs = "; ".join(err.get("message", str(err)) for err in payload["errors"])
        raise PRViewerError(f"GraphQL errors: {msgs}")

    container = payload.get("data", {}).get(container_key)
    if container is None:
        raise PRViewerError(f"no such user or no data returned for {user!r}")

    login = container.get("login", user or "?")
    nodes = container["pullRequests"]["nodes"]
    return login, nodes


# ---------------------------------------------------------------------------
# Status check bucketing
# ---------------------------------------------------------------------------

def _check_name(node):
    if node.get("__typename") == "StatusContext":
        return node.get("context") or ""
    return node.get("name") or ""


def _check_timestamp(node):
    """Best-available timestamp for ordering runs of the same check."""
    return (
        node.get("completedAt")
        or node.get("startedAt")
        or node.get("createdAt")
        or ""
    )


def _dedupe_contexts(nodes):
    """Collapse multiple runs of the same check name to the most recent one.

    GitHub's PR UI shows only the latest run per check name; without this a
    stale run (e.g. a CANCELLED retry) can mask the current passing result.
    ISO 8601 timestamps sort lexicographically, so max() picks the newest.
    """
    latest = {}
    for node in nodes:
        name = _check_name(node)
        existing = latest.get(name)
        if existing is None or _check_timestamp(node) >= _check_timestamp(existing):
            latest[name] = node
    return list(latest.values())


def _bucket_for(name):
    if any(sub in name for sub in E2E_SUBSTRINGS) or name.startswith("cypress:"):
        return "e2e"
    return "other"


def _normalize_check_state(node):
    """Map a check/context node to one of: failure, pending, success, neutral."""
    if node.get("__typename") == "StatusContext":
        state = (node.get("state") or "").upper()
        if state in ("FAILURE", "ERROR"):
            return "failure"
        if state in ("PENDING", "EXPECTED"):
            return "pending"
        if state == "SUCCESS":
            return "success"
        return "neutral"

    # CheckRun: completed runs carry a conclusion; otherwise it's still running.
    status = (node.get("status") or "").upper()
    conclusion = (node.get("conclusion") or "").upper()
    if status != "COMPLETED":
        # QUEUED / IN_PROGRESS / PENDING / WAITING / REQUESTED
        return "pending"
    if conclusion in ("FAILURE", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED", "CANCELLED"):
        return "failure"
    if conclusion in ("SUCCESS",):
        return "success"
    if conclusion in ("SKIPPED", "STALE"):
        return "skipped"
    # NEUTRAL or anything else
    return "neutral"


def bucket_checks(pr):
    """Return {bucket: {"state": ..., "passed": n, "total": n}} for a PR."""
    buckets = {
        "other": [],
        "e2e": [],
    }
    rollup = pr.get("statusCheckRollup")
    if rollup:
        for node in _dedupe_contexts(rollup["contexts"]["nodes"]):
            name = _check_name(node)
            # The require-review check is informational noise; drop it entirely.
            if name in REQUIRE_REVIEW_CHECKS:
                continue
            state = _normalize_check_state(node)
            # Skipped checks shouldn't count for or against a bucket.
            if state == "skipped":
                continue
            buckets[_bucket_for(name)].append(state)

    result = {}
    for bucket, states in buckets.items():
        total = len(states)
        if total == 0:
            result[bucket] = {"state": "none", "passed": 0, "total": 0}
            continue
        passed = sum(1 for s in states if s == "success")
        if any(s == "failure" for s in states):
            state = "failure"
        elif any(s == "pending" for s in states):
            state = "pending"
        elif passed > 0:
            state = "success"
        else:
            state = "neutral"
        result[bucket] = {"state": state, "passed": passed, "total": total}
    return result


# ---------------------------------------------------------------------------
# Review state
# ---------------------------------------------------------------------------

def review_state(pr):
    """Return (state_class, label) describing the PR's review status."""
    decision = pr.get("reviewDecision")
    reviews = (pr.get("latestReviews") or {}).get("nodes") or []
    if decision == "APPROVED":
        approvals = sum(1 for r in reviews if (r.get("state") or "") == "APPROVED")
        if approvals > 1:
            return "approved", f"Approved {approvals}x"
        return "approved", "Approved"
    if decision == "CHANGES_REQUESTED":
        return "changes", "Changes requested"

    commenters = {
        (r.get("author") or {}).get("login")
        for r in reviews
        if (r.get("state") or "") == "COMMENTED"
    }
    commenters.discard(None)

    # If a reviewer has been re-requested, their earlier comment is stale.
    requested = {
        (n.get("requestedReviewer") or {}).get("login")
        for n in (pr.get("reviewRequests") or {}).get("nodes") or []
    }
    requested.discard(None)

    # Only show "Commented" if at least one commenter hasn't been re-requested.
    if commenters - requested:
        return "commented", "Commented"
    return "none", "No reviews"


# ---------------------------------------------------------------------------
# Hierarchy / stack detection
# ---------------------------------------------------------------------------

def build_forest(prs):
    """Group PRs by repo and build a parent->children forest per repo.

    Returns a list of (repo_name, roots) where roots is a list of PR dicts;
    each PR dict gets a "_children" list attached.
    """
    by_repo = defaultdict(list)
    for pr in prs:
        by_repo[pr["repository"]["nameWithOwner"]].append(pr)

    repo_groups = []
    for repo in sorted(by_repo):
        repo_prs = by_repo[repo]
        head_to_pr = {pr["headRefName"]: pr for pr in repo_prs}
        for pr in repo_prs:
            pr["_children"] = []

        roots = []
        for pr in repo_prs:
            parent = head_to_pr.get(pr["baseRefName"])
            if parent is not None and parent is not pr:
                parent["_children"].append(pr)
            else:
                roots.append(pr)

        # Stable ordering: by PR number at every level.
        def sort_tree(node):
            node["_children"].sort(key=lambda p: p["number"])
            for child in node["_children"]:
                sort_tree(child)

        roots.sort(key=lambda p: p["number"])
        for root in roots:
            sort_tree(root)

        repo_groups.append((repo, roots))
    return repo_groups


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

CSS = """
:root {
  color-scheme: light dark;
  --dot-size: .7rem;   /* PR status dot diameter */
  --dot-gap: .4rem;     /* space between the dot and the title text */

  /* Theme tokens. Dark mode only overrides these values (see the media
     query below); every rule references the vars, so colors live in one place. */
  --fg: #1f2328;            /* body text */
  --bg: #f6f8fa;            /* page background */
  --accent: #0969da;        /* links */
  --border: #d0d7de;        /* hairlines, separators, button borders */
  --muted: #57606a;         /* secondary text */
  --subtle-bg: #eaeef2;     /* branch chips, button hover */
  --draft-text: #6e7781;    /* draft badge text */
  --branch-fg: #57606a;     /* branch chip text */
  --dot-active: #08bf37;    /* non-draft PR status dot */
  --dot-draft: #c1cad4;     /* draft PR status dot */

  --success-bg: #dafbe1; --success-fg: #1a7f37; --success-border: #b6e9c1;
  --failure-bg: #ffebe9; --failure-fg: #cf222e; --failure-border: #ffc1bc;
  --pending-bg: #fff8c5; --pending-fg: #9a6700; --pending-border: #f0e2a0;
  --neutral-bg: #f0f2f4; --neutral-fg: #57606a; --neutral-border: #d8dde3;
}
@media (prefers-color-scheme: dark) {
  :root {
    --fg: #cdd9e5;
    --bg: #22272e;
    --accent: #539bf5;
    --border: #444c56;
    --muted: #768390;
    --subtle-bg: #2d333b;
    --draft-text: #768390;
    --branch-fg: #adbac7;
    --dot-active: #6bc46d;
    --dot-draft: #768390;

    --success-bg: #1b3329; --success-fg: #6bc46d; --success-border: #2b5a3e;
    --failure-bg: #3a2426; --failure-fg: #e5707a; --failure-border: #62383c;
    --pending-bg: #3a3320; --pending-fg: #d9b850; --pending-border: #5e5230;
    --neutral-bg: #2d333b; --neutral-fg: #909dab; --neutral-border: #444c56;
  }
}
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  margin: 2rem auto; max-width: 60rem; padding: 0 1rem; line-height: 1.5;
  color: var(--fg); background: var(--bg);
}
h1 { font-size: 1.5rem; margin-bottom: .5rem; }
nav.nav { display: flex; flex-wrap: wrap; align-items: center; gap: .75rem; margin-bottom: 1.5rem; }
nav.nav a { color: var(--accent); text-decoration: none; font-size: .9rem; }
nav.nav a:hover { text-decoration: underline; }
nav.nav a:not(:last-child)::after { content: "|"; color: var(--border); margin-left: .75rem; }
h2 { font-size: 1.1rem; margin-top: 2rem; border-bottom: 1px solid var(--border); padding-bottom: .3rem; }
h2 a.repo-link {
  color: inherit;
  text-decoration: underline;
}
h2 a.repo-link:hover { color: var(--accent); }
ul.tree { list-style: none; padding-left: 0; }
ul.tree ul.tree { padding-left: 1.4rem; border-left: 2px solid var(--border); margin-left: calc(.4rem + var(--dot-size) + var(--dot-gap)); }
li.pr { margin: 1rem 0; }
.pr-row { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem; }
.pr-title a { color: var(--accent); text-decoration: none; font-weight: 600; }
.pr-title a:hover { text-decoration: underline; }
.status-dot {
  display: inline-block; width: var(--dot-size); height: var(--dot-size); border-radius: 50%;
  margin-right: var(--dot-gap); flex: none; background: var(--dot-active);
}
.status-dot.is-draft { background: var(--dot-draft); }
.draft { font-size: .75rem; background: transparent; color: var(--draft-text); border: 1px solid var(--border); border-radius: 1rem; padding: 0 .5rem; }
.checks { display: flex; flex-wrap: wrap; gap: .35rem; margin-top: .25rem; margin-left: calc(var(--dot-size) + var(--dot-gap)); }
.pill {
  font-size: .75rem; border-radius: 1rem; padding: .1rem .6rem;
  white-space: nowrap; border: 1px solid transparent;
}
.pill.success, .pill.approved  { background: var(--success-bg); color: var(--success-fg); border-color: var(--success-border); }
.pill.failure, .pill.changes   { background: var(--failure-bg); color: var(--failure-fg); border-color: var(--failure-border); }
.pill.pending, .pill.commented { background: var(--pending-bg); color: var(--pending-fg); border-color: var(--pending-border); }
.pill.neutral, .pill.none      { background: var(--neutral-bg); color: var(--neutral-fg); border-color: var(--neutral-border); }
.branches { display: inline-flex; align-items: center; gap: .35rem; flex-wrap: wrap; }
.branch { display: inline-flex; align-items: center; gap: .2rem; }
.branch-name {
  font-size: .75rem; background: var(--subtle-bg); color: var(--branch-fg);
  border-radius: .4rem; padding: .05rem .4rem;
}
.branch-arrow { color: var(--muted); font-size: .8rem; }
.copy-btn {
  font-size: .7rem; line-height: 1; cursor: pointer;
  background: transparent; border: 1px solid var(--border); border-radius: .4rem;
  color: var(--muted); padding: .1rem .3rem;
}
.copy-btn:hover { background: var(--subtle-bg); }
.copy-btn.copied { color: var(--success-fg); border-color: var(--success-border); }
.empty { color: var(--muted); font-style: italic; }
"""

CHECK_GLYPH = {
    "success": "✓",
    "failure": "✗",
    "pending": "⏳",
    "neutral": "–",
    "none": "–",
}


COPY_SCRIPT = """
document.addEventListener('click', function (e) {
  var btn = e.target.closest('.copy-btn');
  if (!btn) return;
  var name = btn.getAttribute('data-branch');
  navigator.clipboard.writeText(name).then(function () {
    var old = btn.textContent;
    btn.textContent = '✓';
    btn.classList.add('copied');
    setTimeout(function () {
      btn.textContent = old;
      btn.classList.remove('copied');
    }, 1000);
  });
});
"""


def render_branch(name):
    safe = html.escape(name)
    attr = html.escape(name, quote=True)
    return (
        '<span class="branch">'
        f'<code class="branch-name">{safe}</code>'
        f'<button type="button" class="copy-btn" data-branch="{attr}" '
        f'title="Copy branch name" aria-label="Copy branch name">⧉</button>'
        '</span>'
    )


def render_pill(label, info):
    glyph = CHECK_GLYPH[info["state"]]
    # Counts only add signal when something isn't passing.
    if info["total"] and info["state"] != "success":
        count = f" {info['passed']}/{info['total']}"
    else:
        count = ""
    return (
        f'<span class="pill {info["state"]}">'
        f'{html.escape(label)} {glyph}{count}</span>'
    )


def render_pr(pr, is_root=True):
    checks = bucket_checks(pr)
    rstate, rlabel = review_state(pr)

    number = pr["number"]
    title = html.escape(pr["title"])
    url = html.escape(pr["url"], quote=True)
    is_draft = pr.get("isDraft")
    dot_cls = "status-dot is-draft" if is_draft else "status-dot"
    dot_title = ' title="Draft"' if is_draft else ""
    status_dot = f'<span class="{dot_cls}"{dot_title}></span>'
    draft = '<span class="draft">draft</span>' if is_draft else ""

    # Top-level PRs show `base ← head`; stacked children show only their own
    # head, since their base is the parent's head shown one level up.
    base = pr["baseRefName"]
    head = pr["headRefName"]
    if is_root:
        branch_label = (
            '<span class="branches">'
            f'{render_branch(base)}'
            '<span class="branch-arrow">←</span>'
            f'{render_branch(head)}'
            '</span>'
        )
    else:
        branch_label = f'<span class="branches">{render_branch(head)}</span>'

    approval = f'<span class="pill {rstate}">{html.escape(rlabel)}</span>'
    check_pills = "".join([
        render_pill("Main", checks["other"]),
        render_pill("E2E", checks["e2e"]),
    ])

    children_html = ""
    if pr["_children"]:
        children_html = (
            '<ul class="tree">'
            + "".join(render_pr(c, is_root=False) for c in pr["_children"])
            + "</ul>"
        )

    return (
        '<li class="pr">'
        '<div class="pr-row">'
        f'<span class="pr-title">{status_dot}<a href="{url}">#{number}</a> {title}</span>'
        f'{draft}{branch_label}'
        '</div>'
        f'<div class="checks">{check_pills}{approval}</div>'
        f'{children_html}'
        '</li>'
    )


def render_html(login, repo_groups):
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Open PRs for {html.escape(login)}</title>",
        f"<style>{CSS}</style></head><body>",
        f"<h1>Open PRs for {html.escape(login)}</h1>",
        '<nav class="nav">'
        '<a href="https://github.com/pulls?q=is%3Apr+author%3A%40me+archived%3Afalse+is%3Aclosed">Closed PRs</a>'
        '<a href="https://github.com/pulls?q=is%3Apr+is%3Aopen+user-review-requested%3A%40me+archived%3Afalse+">Review queue</a>'
        '<a href="https://github.com/pulls?q=is%3Apr+label%3Aaudit+-label%3Areviewed+-review%3Aapproved+user-review-requested%3A%40me+archived%3Afalse">Audit queue</a>'
        '</nav>',
    ]

    total_prs = sum(
        1 for _, roots in repo_groups for _ in _walk(roots)
    )
    if total_prs == 0:
        parts.append('<p class="empty">No open pull requests found.</p>')
    else:
        for repo, roots in repo_groups:
            repo_url = html.escape(f"https://github.com/{repo}", quote=True)
            parts.append(
                f'<h2><a class="repo-link" href="{repo_url}">{html.escape(repo)}</a></h2>'
            )
            parts.append('<ul class="tree">')
            parts.extend(render_pr(r) for r in roots)
            parts.append("</ul>")

    parts.append(f"<script>{COPY_SCRIPT}</script>")
    parts.append("</body></html>")
    return "\n".join(parts)


def _walk(nodes):
    for n in nodes:
        yield n
        yield from _walk(n["_children"])


def render_page(user):
    """Fetch `user`'s PRs and return (login, html_doc, pr_count).

    Raises PRViewerError on any fetch/GraphQL failure.
    """
    login, prs = fetch_prs(user)
    repo_groups = build_forest(prs)
    return login, render_html(login, repo_groups), len(prs)
