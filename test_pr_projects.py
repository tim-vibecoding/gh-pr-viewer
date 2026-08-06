import unittest

import pr_core
import pr_projects as p
import pr_store


def make_pr(number, repo="o/r", state="OPEN", base="main", head=None, checks=()):
    return {
        "number": number,
        "title": f"PR {number}",
        "url": f"https://github.com/{repo}/pull/{number}",
        "state": state,
        "isDraft": False,
        "author": {"login": "me"},
        "baseRefName": base,
        "headRefName": head or f"branch-{number}",
        "repository": {"nameWithOwner": repo, "defaultBranchRef": {"name": "main"}},
        "labels": {"nodes": []},
        "reviewRequests": {"nodes": []},
        "reviewDecision": None,
        "reviews": {"nodes": []},
        "statusCheckRollup": {"contexts": {"nodes": list(checks)}},
        "_children": [],
    }


def make_project(name="P", entries=(), description=""):
    return {
        "id": "abc123",
        "name": name,
        "description": description,
        "touched_at": "2026-01-01T00:00:00Z",
        "show_closed": False,
        "entries": [dict(e) for e in entries],
    }


def entry(number, repo="o/r", note=""):
    return {"repo": repo, "number": number, "note": note}


EMPTY_STORE = {"version": 1, "projects": []}


class ParsePrRefTest(unittest.TestCase):
    def test_full_url(self):
        self.assertEqual(
            p.parse_pr_ref("https://github.com/khan/webapp/pull/4821"),
            ("khan/webapp", 4821),
        )

    def test_url_with_trailing_path_query_and_fragment(self):
        for suffix in ("/files", "?w=1", "#discussion_r1", "/files#diff-abc"):
            self.assertEqual(
                p.parse_pr_ref(f"https://github.com/khan/webapp/pull/4821{suffix}"),
                ("khan/webapp", 4821),
                suffix,
            )

    def test_shorthand_forms(self):
        self.assertEqual(p.parse_pr_ref("khan/webapp#4821"), ("khan/webapp", 4821))
        self.assertEqual(p.parse_pr_ref("khan/webapp/pull/4821"), ("khan/webapp", 4821))
        self.assertEqual(p.parse_pr_ref("  khan/webapp#4821  "), ("khan/webapp", 4821))

    def test_bare_number_is_rejected(self):
        # Without a repo it's a guess, and guessing wrong files a stranger's PR.
        self.assertIsNone(p.parse_pr_ref("4821"))
        self.assertIsNone(p.parse_pr_ref("#4821"))

    def test_junk_is_rejected(self):
        for bad in ("", "   ", "not a ref", "khan/webapp", "https://example.com/a/b/pull/1",
                    "khan/web app#1", "khan/webapp#abc"):
            self.assertIsNone(p.parse_pr_ref(bad), bad)


class CountLineTest(unittest.TestCase):
    def test_open_and_closed(self):
        self.assertEqual(p.count_line(9, 3), "12 PRs · 9 open, 3 closed")

    def test_all_open(self):
        self.assertEqual(p.count_line(4, 0), "4 PRs · 4 open")

    def test_all_closed(self):
        self.assertEqual(p.count_line(0, 3), "3 PRs · all closed")

    def test_unavailable_is_called_out(self):
        self.assertEqual(p.count_line(1, 0, 1), "2 PRs · 1 open, 1 unavailable")

    def test_unknown_split_omits_it_rather_than_guessing(self):
        self.assertEqual(p.count_line(12, 0, known=False), "12 PRs")

    def test_singular_and_empty(self):
        self.assertEqual(p.count_line(1, 0), "1 PR · 1 open")
        self.assertEqual(p.count_line(0, 0), "0 PRs")


class NoteTextTest(unittest.TestCase):
    def test_escapes_html(self):
        self.assertEqual(p.render_note_text("<b>x</b>"), "&lt;b&gt;x&lt;/b&gt;")

    def test_linkifies_urls(self):
        out = p.render_note_text("see https://example.com/a for why")
        self.assertIn('<a href="https://example.com/a">https://example.com/a</a>', out)
        self.assertTrue(out.startswith("see "))

    def test_trailing_punctuation_is_not_part_of_the_link(self):
        out = p.render_note_text("see https://example.com/a.")
        self.assertIn('href="https://example.com/a"', out)
        self.assertTrue(out.endswith("."))

    def test_javascript_urls_are_not_linkified(self):
        out = p.render_note_text("javascript:alert(1)")
        self.assertNotIn("<a", out)


class StackedHintTest(unittest.TestCase):
    def test_detects_a_parent_inside_the_project(self):
        project = make_project(entries=[entry(1), entry(2)])
        prs = {
            ("o/r", 1): make_pr(1, base="main", head="feature-a"),
            ("o/r", 2): make_pr(2, base="feature-a", head="feature-b"),
        }
        hints = p._stacked_hints(project, prs)
        self.assertEqual(hints, {("o/r", 2): 1})

    def test_no_hint_when_the_parent_is_not_in_the_project(self):
        project = make_project(entries=[entry(2)])
        prs = {("o/r", 2): make_pr(2, base="feature-a", head="feature-b")}
        self.assertEqual(p._stacked_hints(project, prs), {})

    def test_same_branch_name_in_another_repo_is_not_a_parent(self):
        project = make_project(entries=[entry(1), entry(2, repo="o/other")])
        prs = {
            ("o/r", 1): make_pr(1, head="shared"),
            ("o/other", 2): make_pr(2, repo="o/other", base="shared"),
        }
        self.assertEqual(p._stacked_hints(project, prs), {})

    def test_hint_renders_instead_of_the_branch_label(self):
        project = make_project(entries=[entry(1), entry(2)])
        prs = {
            ("o/r", 1): make_pr(1, base="main", head="feature-a"),
            ("o/r", 2): make_pr(2, base="feature-a", head="feature-b"),
        }
        out = p.render_detail(EMPTY_STORE, project, prs, False, {})
        self.assertIn("stacked on <a href=\"#pr-o-r-1\">#1</a>", out)
        self.assertNotIn("feature-a</code>", out.split("stacked on")[1])


class DetailRowTest(unittest.TestCase):
    def render(self, project, prs, show_closed=False, params=None):
        return p.render_detail(EMPTY_STORE, project, prs, show_closed, params or {})

    def test_open_row_shows_check_pills_and_the_repo(self):
        project = make_project(entries=[entry(1)])
        out = self.render(project, {("o/r", 1): make_pr(1)})
        self.assertIn("Main", out)
        self.assertIn("E2E", out)
        self.assertIn('class="row-repo"', out)

    def test_merged_row_gets_a_merged_pill_and_drops_check_pills(self):
        project = make_project(entries=[entry(1)])
        out = self.render(project, {("o/r", 1): make_pr(1, state="MERGED")},
                          show_closed=True)
        self.assertIn('<span class="pill merged">merged</span>', out)
        self.assertNotIn(">Main ", out)
        self.assertIn("is-closed", out)

    def test_closed_unmerged_row_gets_a_closed_pill(self):
        project = make_project(entries=[entry(1)])
        out = self.render(project, {("o/r", 1): make_pr(1, state="CLOSED")},
                          show_closed=True)
        self.assertIn('<span class="pill closed">closed</span>', out)

    def test_closed_rows_are_hidden_by_default_but_still_counted(self):
        project = make_project(entries=[entry(1), entry(2)])
        prs = {("o/r", 1): make_pr(1), ("o/r", 2): make_pr(2, state="MERGED")}
        out = self.render(project, prs)
        self.assertNotIn('id="pr-o-r-2"', out)
        # The filter states what it hid, in both states.
        self.assertIn("2 PRs · 1 open, 1 closed", out)
        self.assertIn("2 PRs · 1 open, 1 closed", self.render(project, prs, True))

    def test_unavailable_entry_renders_with_a_working_remove(self):
        project = make_project(entries=[entry(7, note="keep me")])
        out = self.render(project, {("o/r", 7): None})
        self.assertIn("unavailable", out)
        self.assertIn("keep me", out)
        self.assertIn('action="/project/entry/remove"', out)
        # Never hidden by the closed filter: Remove is its only fix.
        self.assertIn('id="pr-o-r-7"', out)

    def test_rows_render_in_stored_order(self):
        project = make_project(entries=[entry(3), entry(1), entry(2)])
        prs = {("o/r", n): make_pr(n) for n in (1, 2, 3)}
        out = self.render(project, prs)
        positions = [out.index(f'id="pr-o-r-{n}"') for n in (3, 1, 2)]
        self.assertEqual(positions, sorted(positions))

    def test_arrows_are_disabled_at_the_ends(self):
        project = make_project(entries=[entry(1), entry(2)])
        prs = {("o/r", n): make_pr(n) for n in (1, 2)}
        out = self.render(project, prs)
        first = out.split('id="pr-o-r-1"')[1].split('id="pr-o-r-2"')[0]
        self.assertIn('value="up" aria-label="Move #1 up" title="Move #1 up" disabled', first)
        self.assertNotIn('value="down" aria-label="Move #1 down" title="Move #1 down" disabled', first)

    def test_disabled_ends_follow_the_visible_list_not_the_stored_one(self):
        # #2 is closed and hidden, so #3 is the last *visible* row and its ▼
        # must be disabled — what the UI shows and what a move does agree.
        project = make_project(entries=[entry(1), entry(3), entry(2)])
        prs = {
            ("o/r", 1): make_pr(1),
            ("o/r", 3): make_pr(3),
            ("o/r", 2): make_pr(2, state="MERGED"),
        }
        out = self.render(project, prs)
        last = out.split('id="pr-o-r-3"')[1]
        self.assertIn('value="down" aria-label="Move #3 down" title="Move #3 down" disabled', last)

    def test_empty_note_renders_a_placeholder_that_is_the_edit_affordance(self):
        project = make_project(entries=[entry(1)])
        out = self.render(project, {("o/r", 1): make_pr(1)})
        self.assertIn('href="#note-pr-o-r-1"', out)
        self.assertIn("(add a note)", out)

    def test_note_is_rendered_and_escaped(self):
        project = make_project(entries=[entry(1, note="<script>x</script>")])
        out = self.render(project, {("o/r", 1): make_pr(1)})
        self.assertNotIn("<script>x</script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_remove_confirms_only_when_a_note_would_be_lost(self):
        with_note = self.render(make_project(entries=[entry(1, note="why")]),
                                {("o/r", 1): make_pr(1)})
        self.assertIn("Remove #1?", with_note)
        without = self.render(make_project(entries=[entry(1)]),
                              {("o/r", 1): make_pr(1)})
        self.assertNotIn("Remove #1?", without)


class DetailHeaderTest(unittest.TestCase):
    def test_missing_description_offers_the_placeholder(self):
        out = p.render_detail(EMPTY_STORE, make_project(), {}, False, {})
        self.assertIn("(add a description)", out)
        self.assertIn('href="#edit-project"', out)

    def test_description_is_shown_in_full(self):
        project = make_project(description="Line one\nLine two")
        out = p.render_detail(EMPTY_STORE, project, {}, False, {})
        self.assertIn("Line one\nLine two", out)

    def test_filter_marks_the_active_state(self):
        project = make_project(entries=[entry(1)])
        prs = {("o/r", 1): make_pr(1)}
        hide = p.render_detail(EMPTY_STORE, project, prs, False, {})
        self.assertIn('href="/projects/abc123?closed=hide" aria-current="page"', hide)
        show = p.render_detail(EMPTY_STORE, project, prs, True, {})
        self.assertIn('href="/projects/abc123?closed=show" aria-current="page"', show)

    def test_add_field_keeps_the_typed_text_after_a_bad_ref(self):
        params = {"flash": ["badref"], "text": ["nonsense"]}
        out = p.render_detail(EMPTY_STORE, make_project(), {}, False, params)
        self.assertIn('value="nonsense"', out)
        self.assertIn("Couldn't find that PR", out)


class EmptyStateTest(unittest.TestCase):
    def test_no_entries(self):
        out = p.render_detail(EMPTY_STORE, make_project(), {}, False, {})
        self.assertIn("No PRs yet", out)
        self.assertIn('href="/"', out)

    def test_everything_filtered_out_says_so_with_a_show_link(self):
        project = make_project(entries=[entry(n) for n in (1, 2, 3)])
        prs = {("o/r", n): make_pr(n, state="MERGED") for n in (1, 2, 3)}
        out = p.render_detail(EMPTY_STORE, project, prs, False, {})
        self.assertIn("All 3 PRs in this project are closed.", out)
        self.assertIn('href="/projects/abc123?closed=show"', out)
        self.assertNotIn('<ul class="entries">', out)


class IndexTest(unittest.TestCase):
    def _store(self, *projects):
        return {"version": 1, "projects": list(projects)}

    def test_no_projects_shows_the_starter_copy(self):
        out = p.render_index(self._store(), None, {}, True, 0, {})
        self.assertIn("Projects are ordered lists of PRs with notes.", out)

    def test_row_shows_counts_and_a_truncatable_blurb(self):
        project = make_project(name="Q3 migration", description="Splitting things.",
                               entries=[entry(1), entry(2)])
        prs = {("o/r", 1): make_pr(1), ("o/r", 2): make_pr(2, state="MERGED")}
        out = p.render_index(self._store(project), None, prs, True, 0, {})
        self.assertIn("Q3 migration", out)
        self.assertIn("2 PRs · 1 open, 1 closed", out)
        self.assertIn("project-blurb", out)
        self.assertIn('href="/projects/abc123"', out)

    def test_a_failed_entry_fetch_degrades_to_a_bare_count(self):
        project = make_project(entries=[entry(1), entry(2)])
        out = p.render_index(self._store(project), None, {}, False, None, {})
        self.assertIn("2 PRs ›", out)
        self.assertNotIn("open,", out)

    def test_footer_link_appears_only_when_there_is_something_to_triage(self):
        with_count = p.render_index(self._store(), None, {}, True, 5, {})
        self.assertIn("5 of your open PRs", with_count)
        self.assertIn("filter=uncategorized", with_count)
        for empty in (0, None):
            self.assertNotIn("of your open PRs", p.render_index(
                self._store(), None, {}, True, empty, {}))

    def _two(self):
        return self._store(dict(make_project(name="First"), id="p1"),
                           dict(make_project(name="Second"), id="p2"))

    def test_rows_carry_move_controls_disabled_at_the_ends(self):
        out = p.render_index(self._two(), None, {}, True, 0, {})
        self.assertIn('action="/project/move"', out)
        self.assertIn('id="project-p1"', out)
        self.assertIn('aria-label="Move “First” up" '
                      'title="Move “First” up" disabled', out)
        self.assertIn('aria-label="Move “Second” down" '
                      'title="Move “Second” down" disabled', out)
        # The middle of each pair is live: first can go down, second can go up.
        self.assertIn('aria-label="Move “First” down" '
                      'title="Move “First” down">', out)
        self.assertIn('aria-label="Move “Second” to the top" '
                      'title="Move “Second” to the top">', out)

    def test_a_lone_project_has_nothing_to_reorder(self):
        out = p.render_index(self._store(make_project()), None, {}, True, 0, {})
        self.assertNotIn('action="/project/move"', out)

    def test_a_read_only_store_shows_no_move_controls(self):
        out = p.render_index(self._two(), "written by a newer version",
                             {}, True, 0, {})
        self.assertNotIn('action="/project/move"', out)

    def test_move_controls_return_to_the_index_with_its_user_filter(self):
        out = p.render_index(self._two(), None, {}, True, 0, {"user": ["someone"]})
        self.assertIn('name="return_to" value="/projects?user=someone"', out)

    def test_store_error_is_shown_rather_than_swallowed(self):
        out = p.render_index(self._store(), "projects.json isn't valid JSON", {}, True, 0, {})
        self.assertIn("isn&#x27;t valid JSON", out)

    def test_duplicate_name_reopens_the_form_prefilled(self):
        params = {"flash": ["dupname"], "name": ["Q3 migration"], "desc": ["why"]}
        out = p.render_index(self._store(), None, {}, True, 0, params)
        self.assertIn("already exists — create anyway?", out)
        self.assertIn('<details class="disclosure" id="new-project" open>', out)
        self.assertIn('value="Q3 migration"', out)
        self.assertIn('name="confirm"', out)
        self.assertIn("Create anyway", out)


class FlashTest(unittest.TestCase):
    def setUp(self):
        self.store = {"version": 1, "projects": [make_project(name="Q3 migration")]}

    def test_exists_names_the_project_and_links_to_the_row(self):
        params = {"flash": ["exists"], "pr": ["4821"], "repo": ["khan/webapp"],
                  "project": ["abc123"]}
        out = p.render_flash(params, self.store)
        self.assertIn("#4821 is already in", out)
        self.assertIn("Q3 migration", out)
        self.assertIn("#pr-khan-webapp-4821", out)

    def test_unknown_code_renders_nothing(self):
        self.assertEqual(p.render_flash({"flash": ["bogus"]}, self.store), "")
        self.assertEqual(p.render_flash({}, self.store), "")

    def test_arguments_are_escaped(self):
        params = {"flash": ["exists"], "pr": ["<img src=x onerror=1>"]}
        out = p.render_flash(params, self.store)
        self.assertNotIn("<img", out)
        self.assertIn("&lt;img", out)

    def test_a_deleted_project_id_does_not_break_the_message(self):
        params = {"flash": ["exists"], "pr": ["1"], "project": ["gone"]}
        out = p.render_flash(params, self.store)
        self.assertIn("#1 is already in this project.", out)

    def test_error_codes_render_as_warnings(self):
        for code in ("badref", "unreadable", "savefail", "error", "gone"):
            out = p.render_flash({"flash": [code]}, self.store)
            self.assertIn('class="flash bad"', out, code)

    def test_a_successful_action_says_nothing(self):
        """The removed confirmations, by their old codes: an action that worked
        is shown by the page it lands on, not announced above it."""
        for code in ("added", "removed", "noted", "moved", "pmoved", "created",
                     "edited", "deleted", "refetched"):
            self.assertEqual(p.render_flash({"flash": [code]}, self.store), "", code)


class DeletePageTest(unittest.TestCase):
    def test_states_exactly_what_is_lost(self):
        project = make_project(name="Q3 migration", entries=[
            entry(1, note="a"), entry(2, note="b"), entry(3),
        ])
        out = p.render_delete_page(project)
        self.assertIn("This removes 3 PRs and 2 notes from the project.", out)
        self.assertIn("The PRs themselves are untouched.", out)
        self.assertIn('action="/project/delete"', out)

    def test_no_notes_is_not_mentioned(self):
        out = p.render_delete_page(make_project(entries=[entry(1)]))
        self.assertIn("This removes 1 PR from the project.", out)


class UrlTest(unittest.TestCase):
    def test_empty_params_are_dropped(self):
        self.assertEqual(p.url("/projects", {"closed": None, "flash": ""}), "/projects")

    def test_anchor_and_params(self):
        self.assertEqual(
            p.url("/projects/x", {"closed": "hide"}, anchor="pr-o-r-1"),
            "/projects/x?closed=hide#pr-o-r-1",
        )

    def test_project_ids_are_escaped_into_the_path(self):
        self.assertEqual(p.project_path("a/b"), "/projects/a%2Fb")


if __name__ == "__main__":
    unittest.main()
