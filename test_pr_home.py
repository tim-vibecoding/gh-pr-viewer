import unittest

import pr_core
import pr_projects as p
import pr_store
from test_pr_projects import make_pr


def store_with(*specs):
    """specs: (name, [(repo, number), ...]) — a store built through pr_store."""
    store = {"version": 1, "projects": []}
    for name, refs in specs:
        project = pr_store.create_project(store, name)
        for repo, number in refs:
            pr_store.add_entry(store, project["id"], repo, number)
    return store


def render(prs, store=None, store_error=None, params=None, interactive=True):
    store = store if store is not None else {"version": 1, "projects": []}
    return p.render_home("me", prs, store, store_error, params or {}, interactive)


class ChipsTest(unittest.TestCase):
    def test_a_membership_shows_a_chip_linking_to_the_project(self):
        store = store_with(("Q3 migration", [("o/r", 1)]))
        out = render([make_pr(1)], store)
        self.assertIn('class="chip"', out)
        self.assertIn("Q3 migration", out)
        self.assertIn("#pr-o-r-1", out)

    def test_a_pr_in_no_project_has_no_chip(self):
        out = render([make_pr(1)], store_with(("Q3", [])))
        self.assertNotIn('class="chip"', out)

    def test_a_pr_in_two_projects_shows_two_chips(self):
        store = store_with(("A", [("o/r", 1)]), ("B", [("o/r", 1)]))
        out = render([make_pr(1)], store)
        self.assertEqual(out.count('class="chip"'), 2)


class AddFormTest(unittest.TestCase):
    def test_offers_a_checkbox_per_project_and_a_new_project_field(self):
        out = render([make_pr(1)], store_with(("A", []), ("B", [])))
        self.assertEqual(out.count('name="project_id"'), 2)
        self.assertIn('name="new_project_name"', out)
        self.assertIn('name="note"', out)
        self.assertIn('action="/pr/add-to-projects"', out)

    def test_an_existing_membership_is_prechecked_and_labelled(self):
        out = render([make_pr(1)], store_with(("A", [("o/r", 1)])))
        self.assertIn("checked", out)
        self.assertIn("already added", out)

    def test_the_form_carries_the_view_it_was_submitted_from(self):
        out = render([make_pr(1)], store_with(("A", [])),
                     params={"filter": ["uncategorized"]})
        self.assertIn('name="return_to" value="/?filter=uncategorized"', out)

    def test_no_projects_yet_still_offers_the_new_project_field(self):
        out = render([make_pr(1)])
        self.assertNotIn('name="project_id"', out)
        self.assertIn('name="new_project_name"', out)


class NextAnchorTest(unittest.TestCase):
    def _anchors(self, out):
        import re
        return re.findall(r'name="next_anchor" value="([^"]*)"', out)

    def test_each_row_points_at_its_successor(self):
        out = render([make_pr(1), make_pr(2), make_pr(3)])
        self.assertEqual(self._anchors(out)[:2], ["pr-o-r-2", "pr-o-r-3"])

    def test_the_last_row_points_at_its_predecessor(self):
        out = render([make_pr(1), make_pr(2), make_pr(3)])
        self.assertEqual(self._anchors(out)[-1], "pr-o-r-2")

    def test_a_lone_row_has_nowhere_to_go(self):
        self.assertEqual(self._anchors(render([make_pr(1)])), [""])

    def test_the_order_follows_the_rendered_tree_not_the_input_list(self):
        # #2 is stacked on #1, so it renders as #1's child — between #1 and #3.
        # The anchors come back in document order (#1, #2, #3), so #1's
        # successor is its own child and #3, being last, points back at #2.
        prs = [make_pr(3), make_pr(1, head="a"), make_pr(2, base="a")]
        out = render(prs)
        self.assertEqual(self._anchors(out), ["pr-o-r-2", "pr-o-r-3", "pr-o-r-2"])


class FilterTest(unittest.TestCase):
    def test_the_ratio_is_stated_in_both_modes(self):
        store = store_with(("A", [("o/r", 1)]))
        prs = [make_pr(1), make_pr(2)]
        for params in ({}, {"filter": ["uncategorized"]}):
            out = render(prs, store, params=params)
            self.assertIn("1 of your 2 open PRs aren", out, params)

    def test_all_is_the_default_and_shows_everything(self):
        out = render([make_pr(1), make_pr(2)], store_with(("A", [("o/r", 1)])))
        self.assertIn('id="pr-o-r-1"', out)
        self.assertIn('id="pr-o-r-2"', out)
        self.assertIn('aria-current="page"', out.split("Uncategorized")[0])

    def test_uncategorized_drops_categorized_rows(self):
        store = store_with(("A", [("o/r", 1)]))
        out = render([make_pr(1), make_pr(2)], store,
                     params={"filter": ["uncategorized"]})
        self.assertNotIn('id="pr-o-r-1"', out)
        self.assertIn('id="pr-o-r-2"', out)

    def test_counts_ride_on_the_control_itself(self):
        out = render([make_pr(1), make_pr(2)], store_with(("A", [("o/r", 1)])))
        self.assertIn("All 2", out)
        self.assertIn("Uncategorized 1", out)

    def test_triaged_to_empty_reads_as_a_success(self):
        store = store_with(("A", [("o/r", 1)]))
        out = render([make_pr(1)], store, params={"filter": ["uncategorized"]})
        self.assertIn("Every open PR is in a project.", out)
        self.assertIn("Back to all PRs", out)

    def test_an_unknown_filter_value_falls_back_to_all(self):
        store = store_with(("A", [("o/r", 1)]))
        out = render([make_pr(1)], store, params={"filter": ["nonsense"]})
        self.assertIn('id="pr-o-r-1"', out)


class PruneTest(unittest.TestCase):
    def _groups(self, prs, member_refs):
        store = store_with(("Q3 migration", member_refs))
        member = pr_store.membership(store)
        return p.prune_uncategorized(pr_core.build_forest(prs), member), member

    def test_categorized_prs_are_dropped(self):
        groups, _ = self._groups([make_pr(1), make_pr(2)], [("o/r", 1)])
        numbers = [pr["number"] for _repo, roots in groups for pr in pr_core._walk(roots)]
        self.assertEqual(numbers, [2])

    def test_a_repo_with_nothing_left_drops_out_entirely(self):
        groups, _ = self._groups([make_pr(1)], [("o/r", 1)])
        self.assertEqual(groups, [])

    def test_a_survivor_whose_parent_was_dropped_is_promoted(self):
        prs = [make_pr(1, head="a"), make_pr(2, base="a", head="b")]
        groups, _ = self._groups(prs, [("o/r", 1)])
        _repo, roots = groups[0]
        self.assertEqual([pr["number"] for pr in roots], [2])
        self.assertEqual(roots[0]["_promoted_under"]["number"], 1)

    def test_a_survivor_keeps_its_own_uncategorized_children_nested(self):
        prs = [make_pr(1, head="a"), make_pr(2, base="a", head="b")]
        groups, _ = self._groups(prs, [])
        _repo, roots = groups[0]
        self.assertEqual([c["number"] for c in roots[0]["_children"]], [2])

    def test_a_promoted_row_renders_as_a_root_with_a_hint_naming_the_project(self):
        prs = [make_pr(1, head="settings-loader"), make_pr(2, base="settings-loader")]
        store = store_with(("Q3 migration", [("o/r", 1)]))
        out = p.render_home("me", prs, store, None, {"filter": ["uncategorized"]})
        self.assertIn("stacked on", out)
        self.assertIn("#1 — in Q3 migration", out)
        # Rendering as a root means the full `base ← branch` label is back.
        self.assertIn("branch-arrow", out)
        # …and the hint links to the parent's row in that project.
        self.assertIn("#pr-o-r-1", out)


class DegradationTest(unittest.TestCase):
    def setUp(self):
        self.out = render([make_pr(1)], store_error="projects.json isn't valid JSON")

    def test_the_pr_list_still_renders(self):
        self.assertIn('id="pr-o-r-1"', self.out)

    def test_no_chips_no_controls_no_filter(self):
        self.assertNotIn('class="chip"', self.out)
        self.assertNotIn("+ Project", self.out)
        self.assertNotIn("Uncategorized", self.out)
        self.assertNotIn('href="/projects"', self.out)

    def test_it_says_why_rather_than_reading_as_no_projects(self):
        self.assertIn("Project data is unavailable", self.out)
        self.assertIn("isn&#x27;t valid JSON", self.out)

    def test_refresh_survives_because_it_has_nothing_to_do_with_the_store(self):
        self.assertIn('action="/cache/clear"', self.out)


class RefreshOnHomeTest(unittest.TestCase):
    def test_it_posts_back_to_the_view_you_are_looking_at(self):
        out = render([make_pr(1)], store_with(),
                     params={"filter": ["uncategorized"], "user": ["someone"]})
        self.assertIn(
            'name="return_to" value="/?filter=uncategorized&amp;user=someone"', out
        )


class CliModeTest(unittest.TestCase):
    def setUp(self):
        store = store_with(("Q3 migration", [("o/r", 1)]))
        self.out = render([make_pr(1)], store, interactive=False)

    def test_chips_survive_because_they_are_genuinely_useful(self):
        self.assertIn('class="chip"', self.out)
        self.assertIn("Q3 migration", self.out)

    def test_controls_are_omitted_because_they_would_post_nowhere(self):
        self.assertNotIn("<form", self.out)
        self.assertNotIn("+ Project", self.out)
        self.assertNotIn("Uncategorized", self.out)


class FlashOnHomeTest(unittest.TestCase):
    def _store_and_params(self, code):
        store = store_with(("Q3 migration", [("o/r", 1)]))
        return store, {"flash": [code], "pr": ["1"], "repo": ["o/r"],
                       "project": [store["projects"][0]["id"]]}

    def test_the_message_sits_above_the_list_not_on_the_row(self):
        store, params = self._store_and_params("exists")
        out = render([make_pr(1)], store, params=params)
        self.assertIn("#1 is already in", out)
        self.assertLess(out.index("is already in"), out.index('id="pr-o-r-1"'))

    def test_a_successful_add_leaves_no_message_behind(self):
        store, params = self._store_and_params("added")
        out = render([make_pr(1)], store, params=params)
        self.assertNotIn('class="flash"', out)
        self.assertNotIn("added to", out)


if __name__ == "__main__":
    unittest.main()
