# Plan: branch labels with base←branch display and copy buttons

## Goal

Update the branch labels rendered for each PR so that:

1. Every **top-level** PR (a forest root) shows a `base ← branch` label
   (e.g. `main ← my-feature`).
2. Every **stacked** PR (a child in the forest) shows **only its own branch**
   (`my-feature`) — its base is its parent's head, which is already visible one
   level up, so repeating it is noise.
3. Every rendered **branch name** has a small copy button next to it that copies
   that branch name to the clipboard.

All changes are in `pr_core.py` (rendering + CSS) plus a small bit of inline
JavaScript. No data-fetching changes are needed: `baseRefName` and
`headRefName` are already requested in `PR_FRAGMENT` and grouped by
`build_forest`.

## Background (current behavior)

- `build_forest` (pr_core.py:260) returns `(repo, roots)` per repo. A PR is a
  **root** when its `baseRefName` is not the head branch of another open PR;
  otherwise it is attached to that parent's `_children` and is a **stacked** PR.
- `render_html` (pr_core.py:439) renders roots via `render_pr(r)`, and
  `render_pr` (pr_core.py:404) recurses into `_children`. So today `render_pr`
  has no way to tell whether it's rendering a root or a stacked child.
- The only branch text shown today is `base_note` (pr_core.py:394): a
  `(base: <branch>)` span that appears *only* when the base differs from the
  repo default branch. There is no head-branch display, and the page contains
  no JavaScript.

## Changes

### 1. Teach `render_pr` whether a PR is top-level

Add an `is_root` parameter:

```python
def render_pr(pr, is_root=True):
    ...
    children_html = ""
    if pr["_children"]:
        children_html = (
            '<ul class="tree">'
            + "".join(render_pr(c, is_root=False) for c in pr["_children"])
            + "</ul>"
        )
```

`render_html` already calls `render_pr(r)` for roots, so the default of
`is_root=True` covers those; only the recursive call passes `is_root=False`.

### 2. Add a branch-label helper

A small helper renders one branch name plus its copy button. The branch name
goes in a `data-branch` attribute (HTML-escaped, quoted) so the JS can read the
exact value without worrying about display markup:

```python
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
```

### 3. Build the branch label in `render_pr`

Replace the `base_note` block (pr_core.py:390-395) with:

```python
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
```

Then swap `{base_note}` for `{branch_label}` in the returned markup
(pr_core.py:416). The `default_branch` lookup that fed `base_note` is no longer
needed and can be removed.

Placement: put the branch label on its own line under the title (a new
`<div class="branch-row">…</div>` between the `pr-row` div and the
`checks` div) so long titles and branch labels don't crowd each other. (Keeping
it inside `pr-row` is an alternative, but a dedicated row reads better given
two branch names + buttons on roots.)

### 4. CSS

Add to the `CSS` string (pr_core.py:303). The existing `.base-note` rule can be
dropped since nothing emits that class anymore.

```css
.branch-row { margin-left: .95rem; margin-top: .2rem; }
.branches { display: inline-flex; align-items: center; gap: .35rem; flex-wrap: wrap; }
.branch { display: inline-flex; align-items: center; gap: .2rem; }
.branch-name {
  font-size: .75rem; background: #eaeef2; color: #57606a;
  border-radius: .4rem; padding: .05rem .4rem;
}
.branch-arrow { color: #57606a; font-size: .8rem; }
.copy-btn {
  font-size: .7rem; line-height: 1; cursor: pointer;
  background: transparent; border: 1px solid #d0d7de; border-radius: .4rem;
  color: #57606a; padding: .1rem .3rem;
}
.copy-btn:hover { background: #eaeef2; }
.copy-btn.copied { color: #1a7f37; border-color: #b6e9c1; }
```

Plus dark-mode overrides in the existing `@media (prefers-color-scheme: dark)`
block:

```css
.branch-name { background: #2d333b; color: #adbac7; }
.branch-arrow { color: #768390; }
.copy-btn { border-color: #444c56; color: #768390; }
.copy-btn:hover { background: #2d333b; }
.copy-btn.copied { color: #6bc46d; border-color: #2b5a3e; }
```

### 5. Copy-to-clipboard JavaScript

The page is currently static. Add a small inline `<script>` near the end of
`render_html` (before `</body>`). Use event delegation so it covers every
button without per-element handlers, and give brief visual feedback:

```javascript
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
```

Add it as a string constant (e.g. `COPY_SCRIPT`) and append
`f"<script>{COPY_SCRIPT}</script>"` to `parts` before `</body></html>`
(pr_core.py:445).

## Out of scope / notes

- No GraphQL / fetch changes — all required fields already exist.
- `pr_viewer.py` and `pr_server.py` need no changes; they call `render_page`
  unchanged.
- A root PR whose base is a merged/closed parent will now plainly show that base
  branch in the `base ← head` label, which also covers the old `base_note` case.

## Verification

1. Run the viewer (`python pr_viewer.py` or the server) against an account that
   has both a standalone PR and a stack of 2+ PRs.
2. Confirm a top-level PR shows `base ← branch`, and each stacked child shows
   only its own branch.
3. Confirm every branch name has a copy button; clicking it copies the exact
   branch name and briefly shows a ✓.
4. Check both light and dark color schemes.
