# UI plan: projects

How the projects feature should *operate*. No implementation detail beyond what
is needed to pin down behavior — no data model, no function names, no CSS.

## Goal (from PROMPT.md)

1. Create projects, each with a name and an optional description.
2. Add a PR to a project, with a note.
3. A page per project where you can reorder its PRs, edit the notes, and
   include or exclude closed PRs.
4. A way to see only the open PRs that aren't in any project yet.

## Design principles

These follow the app as it already works, and they constrain every interaction
below:

- **Server-rendered pages, one action per click.** Every mutating control is a
  small form that posts, then lands you back on the page you were on. There is
  no client-side state to get out of sync with the server, and no "save all"
  button that can be forgotten. Existing inline JS stays limited to progressive
  nice-to-haves (like today's copy button) — nothing essential may depend on it.
- **Refresh is always safe and always current.** The page refetches from GitHub
  on each request, so PR status on a project page is as live as the home page.
  The corollary: no auto-refresh, ever, because it would blow away a
  half-typed note.
- **View state lives in the URL.** Which project you're looking at, whether
  closed PRs are shown, and whether home is filtered to uncategorized PRs are
  all in the URL — so refresh, back/forward, bookmarking, and sending someone a
  link all behave the way they look like they should.
- **A filter states what it hid.** Both filters here keep a count of the
  excluded set visible in the header, so a narrowed view can never be mistaken
  for the whole picture.
- **Your ordering is never silently overridden.** The home page auto-groups PRs
  by repo and auto-builds stacks. A project is the opposite: the order is
  yours, and nothing reorders it but you.

## Pages

Three surfaces. The first already exists.

```
Open PRs (home)  ──►  Projects (index)  ──►  Project detail
  All / Uncategorized      │                      ▲
     │   ▲                 │                      │
     │   └── "5 not in any project" ──┘           │
     └──────────── add PR to project ─────────────┘
```

Home has two modes — **All** and **Uncategorized** — rather than being two
pages. Same list, same rows, one filter; a separate "uncategorized" page would
duplicate the whole PR list for one predicate.

### 1. Home — "Open PRs" (existing page, small additions)

Two additions on the PR row, plus a filter in the header.

- **Project chips.** If a PR belongs to any projects, its row shows a small
  chip per project, linking to that project page. This is how you discover from
  your daily list that a PR is already tracked somewhere.
- **"+ Project" control.** A disclosure on the row that opens in place (no
  navigation, no modal, no page jump) and contains one compact form:
  - a checkbox per existing project, with any project the PR is already in
    pre-checked and labeled "already added";
  - a one-line **Note** field (optional);
  - a **New project…** name field, for filing a PR into a project that doesn't
    exist yet — creating and adding in one action, so you never have to leave
    your list, create a project, come back, and find the PR again. No
    description field here; a project born mid-flow can be described later on
    its own page, and asking for prose in the middle of triage is friction;
  - an **Add** button.

  Submitting posts, reloads the home page scrolled back to that same PR, and
  shows a confirmation naming the project(s), e.g.
  *"#4821 added to Q3 migration."* with an **Edit note** link into the project
  page.

The nav gains an internal **Projects** link. Today's nav is all outbound GitHub
links; the internal link should be visually distinct from those so it doesn't
read as "leaving to github.com".

#### The uncategorized filter

```
Open PRs for tmccabe                       ( All 12 ) ( Uncategorized 5 )

5 of your 12 open PRs aren't in any project.
──────────────────────────────────────────────────────────────────────
khan/webapp
  ● #4902  Fix the flaky settings test              [ + Project ]
    main ← flaky-settings ⧉
    Main ✓   E2E ✗   No reviews
  ● #4915  Bump the linter                          [ + Project ]
    stacked on #4830 — in Q3 migration
    ...
```

A two-state control in the page header, mirroring the closed-PR filter on
project pages: **All** / **Uncategorized**, with counts on the control itself,
and the choice reflected in the URL.

- **Uncategorized means an open PR with no project chips** — it's in zero
  projects. Nothing else narrows it. Since the filter's effect is "hide every
  row that has chips", the remaining page is visually calm by construction.
- Default is **All**. This is the daily page; the filter is a triage mode you
  step into deliberately, not the thing you land on.
- The header states the ratio (*"5 of your 12 open PRs aren't in any project."*)
  in **both** states, so the filter never lies by omission and you can see
  there's triage to do without switching modes first. In All mode it reads as a
  nudge; in Uncategorized mode it's the count of what you're looking at.
- **Repo grouping is preserved**, and a repo whose PRs are all categorized drops
  out entirely — heading included. An empty repo section reads as a bug.
- Closed PRs never appear here. Home is open-only, which is what the prompt
  asks for; the closed filter belongs to project pages.

**Stacks under the filter.** Hiding a categorized parent whose child is
uncategorized would either orphan the child or drag the parent back in. Neither
is right, so: a surviving PR whose parent was filtered out is **promoted to a
top-level row** and shows the same quiet hint the project page uses, extended
to name where the parent went — `stacked on #4830 — in Q3 migration`, itself a
link to that project. The stack relationship stays legible without pulling
already-triaged PRs back onto the page. Promotion also means such a row shows
the full `base ← branch` label, since it's rendering as a root.

**Triaging to empty.** Adding a PR to a project from this view makes the row
disappear on the next load — that's the point, and the view is designed to be
worked down to nothing. Two consequences:

- The confirmation cannot live on the row, because the row is gone. It goes at
  the top of the list, and still names the project with an **Edit note** link.
- Scroll restoration can't target the removed row either. Land on the row that
  took its place, so a run of adds doesn't throw you back to the top each time.
- The empty state is a success message, not an absence:
  *"Every open PR is in a project."* — with a link back to **All**.

### 2. Projects index

```
Projects                                        [ + New project ]

  Q3 migration            12 PRs · 9 open, 3 closed      ›
  Splitting the monolith settings loader apart
  Flaky test cleanup       4 PRs · 4 open                ›
  Reading group            3 PRs · all closed            ›

  5 of your open PRs aren't in any project →

  (no projects yet → "Projects are ordered lists of PRs with notes.
   Create one to get started.")
```

- Each row: name, PR count broken into open/closed, and the description as a
  muted second line — truncated to one line, since this list is for scanning.
  A project with no description just doesn't show that line. The whole row is a
  link to the project page.
- **+ New project** reveals an inline **name** field, an optional
  **description** field, and a Create button. Only the name is required —
  Create must never be blocked on writing a description. Creating goes straight
  to the new project's page, which is where you'd want to be — the next thing
  you do is add PRs.
- Names need not be unique, but creating a duplicate name warns
  (*"A project named 'Q3 migration' already exists — create anyway?"*) rather
  than blocking, because a rename is cheap and a hard error mid-flow is not.
- **A footer line links to the uncategorized view** — *"5 of your open PRs
  aren't in any project →"* — landing on home in Uncategorized mode. This is
  where you think of that question: you came here to survey what's tracked, and
  the natural follow-up is what isn't. It's a link into home's filter, not a
  fourth page, and it's hidden when the count is zero.
- Rename and delete live on the project page, not here. One place to manage a
  project, not two.
- Ordering of this list is manual, with the same **▲ ▼ ⤒** controls a project
  page gives its PRs — same glyphs, same disabled ends, same land-on-the-row
  reload. Two ordered lists that behave differently would be two things to
  learn. The arrows are hidden when there's nothing to arrange (one project) or
  when the store is read-only.
- Was: most recently touched first. That tracked what you'd last poked, not what
  you care about — a project you're steadily working through kept sinking under
  one you touched once. `touched_at` still records the fact; it just no longer
  decides the order. Existing stores adopt that sort as their starting order, so
  nothing jumps on the first load after this change.

### 3. Project detail — the main page

```
Open PRs › Projects › Q3 migration                      [ Edit ] [ Delete ]

Splitting the monolith settings loader apart. Blocked on the config
change landing first.

12 PRs · 9 open, 3 closed             Closed PRs: ( Hide ) ( Show )

[ Add a PR:  paste a GitHub PR URL or owner/repo#123   ] [ note… ] [ Add ]

──────────────────────────────────────────────────────────────────────
 ▲ ▼   ● #4821  Extract the settings loader        khan/webapp
        main ← settings-loader ⧉
        Main ✓   E2E ✓   Approved
        ▏ Has to land before the config change; Sam has context.
                                              [ Edit note ] [ Remove ]
──────────────────────────────────────────────────────────────────────
 ▲ ▼   ● #4830  Use the new settings loader       khan/webapp
        stacked on #4821
        Main ⏳  E2E ✓   No reviews
        ▏ (add a note)
                                              [ Edit note ] [ Remove ]
──────────────────────────────────────────────────────────────────────
 ▲ ▼   ○ #4744  Delete the old loader             khan/webapp   merged
        ▏ Landed 7/2 — kept for the writeup.
                                              [ Edit note ] [ Remove ]
──────────────────────────────────────────────────────────────────────
```

#### Rows

A row reuses the home page's PR presentation — same status dot, same
`base ← branch` labels with copy buttons, same Main/E2E check pills, same
review-state and bot pills — so the two pages read as one app and a status
means the same thing in both places.

Differences from home, all of them forced by what a project *is*:

- **The repo is shown on every row.** A project can span repos, so there is no
  per-repo grouping header to carry that information.
- **The list is flat.** Stacks are not nested here; nesting would fight your
  manual order. When a PR's parent is also in the project, the row shows a
  quiet `stacked on #4821` hint instead — the relationship is still visible,
  just not structural. (Home's uncategorized filter reuses this same hint for
  the same reason: a stack that can't be drawn as a tree still says so in
  words.)
- **Closed and merged entries** show a `merged` or `closed` pill and are dimmed.
  Their check pills are dropped: checks on a landed PR are noise.

#### Reordering

Each row has **▲** and **▼**. One click moves the PR one slot; the page reloads
and lands scrolled to that row, so repeated clicks work as a smooth walk up or
down the list. The arrow is disabled (visibly, not silently) on the first and
last row.

Rationale: with tens of PRs, up/down is fast enough, survives refresh, needs no
JS, and works from the keyboard. Drag-and-drop is a possible later enhancement
layered on top — never the only way to reorder.

Two behaviors that matter more than the mechanism:

- **Order is absolute and hidden-item-safe.** A PR keeps its position whether
  or not closed PRs are being shown. Moving a PR while closed ones are hidden
  moves it past the hidden ones too, so nothing shuffles unexpectedly when you
  toggle them back on.
- A **Move to top** action on each row, for the common "this is what I'm on
  now" gesture.

#### Notes

- A note sits under the PR, visually attached to it, in a muted style with an
  indent bar. Multi-line, line breaks preserved, no markdown rendering (URLs
  linkified is fine) — these are scratch notes, not documents.
- An empty note renders as a faint **(add a note)** placeholder that is itself
  the edit affordance, so a note-less entry doesn't look broken.
- **Edit note** swaps the note for a textarea plus **Save** and **Cancel**,
  in place, without moving the rest of the list. Saving reloads to the same
  row. Cancel restores the note untouched.
- Several notes can be open for editing at once; each saves independently.
- Editing is per-entry, not per-PR: the same PR in two projects has two
  independent notes. A note is *why this PR is in this list*, which is
  project-specific by nature.
- Notes are the only thing here that isn't recoverable from GitHub, so
  destructive paths guard them — see Removing below.

#### The closed-PR filter

A two-state control in the header: **Hide** / **Show**, reflected in the URL.

- Default is **Hide** — the everyday view is what's still in flight.
- Even when hiding, the header count states what's hidden
  (*"12 PRs · 9 open, 3 closed"*), so the filter never lies by omission.
- **Show** brings closed and merged entries back in their stored positions,
  dimmed. It does not sort them to the bottom: their position is information
  you set.
- The setting is remembered per project between visits, with the URL winning
  when it says something explicit. A shared link shows the recipient what the
  sender saw.
- "Closed" means *not open* — both merged and closed-unmerged. They're
  distinguished by pill, filtered as one group, matching what the prompt asks
  for.

#### The description

- Sits directly under the project name, above the counts — the first thing you
  read after the title, since it's what tells you what the list is *for* when
  you come back to it in three weeks.
- Muted, and shown in full here (unlike the truncated index version).
  Multi-line, line breaks preserved, no markdown rendering — same treatment as
  notes, for the same reason.
- A project with no description shows a faint **(add a description)**
  placeholder that is itself the edit affordance, matching how empty notes
  behave. It should read as optional, not as an unfinished field.
- A note answers *why is this PR here*; a description answers *what is this
  list*. Keeping both means neither one has to do the other's job.

#### Removing, renaming, deleting

- **Remove** takes a PR out of the project. Because it discards a note, it
  confirms in place — the button becomes
  **Remove #4821? [ Remove ] [ Cancel ]** — rather than firing on one click.
  Removing an entry with no note can skip the confirmation.
- **Edit** turns the name and description into inline fields with Save and
  Cancel, in place, without navigating. Both are editable together — they're one
  thought, and splitting them into two separate edit affordances in one header
  would be clutter. Clearing the description is allowed and reverts it to the
  placeholder; that's a normal edit, not a destructive one.
- **Delete project** goes to a confirmation page that states exactly what is
  lost (*"Delete 'Q3 migration'? This removes 12 PRs and 9 notes from the
  project. The PRs themselves are untouched."*). Then back to the index with a
  confirmation message.

#### Adding a PR from this page

The add field accepts a full GitHub PR URL or `owner/repo#123`, plus an
optional note. This is deliberately more permissive than the home page's
control, which can only offer PRs that are yours and open: from here you can
add **anyone's** PR, in any repo you can read, in any state. That's needed for
the review-queue and already-merged cases.

Behavior:

- Unparseable input, or a PR that can't be fetched, keeps your typed text and
  says why (*"Couldn't find that PR — check the URL, or you may not have access
  to that repo."*). Input is never silently discarded.
- Adding a PR already in the project is not an error and never overwrites the
  existing note: it scrolls to the existing entry and says
  *"#4821 is already in this project."*

## Empty and error states

- **Empty project:** *"No PRs yet. Paste a PR URL above, or add one from your
  open PRs."* with a link home.
- **All entries filtered out** (everything closed, closed hidden):
  *"All 3 PRs in this project are closed."* with a Show link — never a bare
  empty list, which reads as data loss.
- **A PR that can't be fetched** (deleted repo, lost access, bad entry): the
  row still renders from what's stored — number, repo, and your note — marked
  **unavailable**, with Remove available. A project must never be made
  unviewable by one bad entry.
- **GitHub fetch fails entirely:** same failure behavior as the existing pages.

## Cross-cutting behavior

- Every mutating action lands you back where you were — same page, same filter,
  same scroll position — with a brief confirmation naming what happened.
- Confirmations are inline text, not modals or timed toasts, so nothing is
  missable and nothing needs dismissing.
- Nothing on any of these pages opens a modal dialog. Reveal in place or
  navigate.
- Keyboard: every control is a real button or link in reading order — reorder
  arrows, note editors, filter toggle. Arrows and toggles carry accessible
  labels (*"Move #4821 up"*, *"Show closed PRs"*) rather than bare glyphs.
- Both color schemes, as elsewhere in the app.

## Implications worth stating up front

These are behavioral consequences, not implementation, but they decide whether
the UI above can keep its promises:

1. **Project membership has to be stored somewhere durable**, and it has to
   outlive a PR closing. The app currently keeps nothing between requests.
2. **Home now reads that store too**, not just project pages — the chips, the
   ratio line, and the uncategorized filter all need membership for every PR in
   the list. So the home page has a membership lookup on every render, and an
   unreadable store must degrade to today's plain list rather than an error page.
3. **The project page cannot rely on the existing open-PR fetch.** It has to
   look up its own entries by repo and number, whatever their state and
   whoever's they are — otherwise the closed filter has nothing to filter and
   any merged PR would vanish from the list.
4. **The server needs to accept mutations, not just GETs.** Today it serves one
   read-only route.
5. **Notes, descriptions, and order are user-authored data** — the only things
   here that can't be recovered from GitHub. They should survive a crash
   mid-action, and no single action should be able to lose more than the one
   note it names.

## Out of scope

- Sharing projects with, or syncing them to, anyone else. Local and single-user.
- Auto-populating projects from a query, label, or milestone. Membership is
  manual.
- Nested projects, per-entry statuses/checkboxes, due dates.
- Auto-archiving a project when everything in it merges.
- **Any "what changed since you last looked" signal** — no "3 PRs merged since
  your last visit" line, no unread markers. A PR that merges just starts
  rendering as merged, and the closed filter is the only thing that decides
  whether you see it. Nothing here tracks when you last viewed a project.
- **Export as markdown** (an ordered list of links plus notes, for pasting into
  a doc or Slack). Would fit the existing copy-button pattern, but it's not part
  of this feature.
