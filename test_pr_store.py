import json
import os
import tempfile
import unittest
from pathlib import Path

import pr_store as s


class StoreTestCase(unittest.TestCase):
    """Points PR_VIEWER_STORE at a fresh temp file for each test."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "projects.json"
        self._prev = os.environ.get("PR_VIEWER_STORE")
        os.environ["PR_VIEWER_STORE"] = str(self.path)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._prev is None:
            os.environ.pop("PR_VIEWER_STORE", None)
        else:
            os.environ["PR_VIEWER_STORE"] = self._prev
        self._dir.cleanup()


class LoadSaveTest(StoreTestCase):
    def test_missing_file_is_not_an_error(self):
        store, err = s.load()
        self.assertIsNone(err)
        self.assertEqual(store["projects"], [])

    def test_round_trip(self):
        store, _ = s.load()
        s.create_project(store, "Q3 migration", "Splitting the loader apart.")
        s.save(store)

        reloaded, err = s.load()
        self.assertIsNone(err)
        self.assertEqual(len(reloaded["projects"]), 1)
        self.assertEqual(reloaded["projects"][0]["name"], "Q3 migration")

    def test_corrupt_file_loads_as_error_and_is_not_overwritten(self):
        self.path.write_text("{not json at all", encoding="utf-8")
        store, err = s.load()
        self.assertIsNotNone(err)
        self.assertEqual(store["projects"], [])
        # The one thing that can't be re-derived from GitHub is the one thing
        # we refuse to clobber: loading must not have touched the file.
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{not json at all")

    def test_newer_version_loads_read_only_with_a_message(self):
        self.path.write_text(
            json.dumps({"version": s.VERSION + 1, "projects": []}), encoding="utf-8"
        )
        _store, err = s.load()
        self.assertIn("newer version", err)

    def test_save_leaves_no_partial_file(self):
        store, _ = s.load()
        s.create_project(store, "P")
        s.save(store)
        leftovers = [p.name for p in self.path.parent.iterdir() if ".tmp-" in p.name]
        self.assertEqual(leftovers, [])
        json.loads(self.path.read_text(encoding="utf-8"))  # parses

    def test_move_aside_renames_and_does_not_lose_data(self):
        self.path.write_text("garbage", encoding="utf-8")
        target = s.move_aside()
        self.assertFalse(self.path.exists())
        self.assertEqual(target.read_text(encoding="utf-8"), "garbage")


class ProjectCrudTest(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store, _ = s.load()

    def test_create_requires_a_name(self):
        with self.assertRaises(s.StoreError):
            s.create_project(self.store, "   ")

    def test_ids_are_distinct(self):
        a = s.create_project(self.store, "A")
        b = s.create_project(self.store, "B")
        self.assertNotEqual(a["id"], b["id"])

    def test_edit_does_not_bump_touched_at(self):
        p = s.create_project(self.store, "A")
        p["touched_at"] = "2000-01-01T00:00:00Z"
        s.edit_project(self.store, p["id"], "A renamed", "why")
        self.assertEqual(p["touched_at"], "2000-01-01T00:00:00Z")
        self.assertEqual(p["name"], "A renamed")
        self.assertEqual(p["description"], "why")

    def test_delete_returns_what_was_removed(self):
        p = s.create_project(self.store, "A")
        removed = s.delete_project(self.store, p["id"])
        self.assertEqual(removed["name"], "A")
        self.assertIsNone(s.get(self.store, p["id"]))

    def test_edit_or_delete_of_a_missing_project_raises(self):
        with self.assertRaises(s.StoreError):
            s.delete_project(self.store, "nope")
        with self.assertRaises(s.StoreError):
            s.edit_project(self.store, "nope", "x", "")

    def test_name_exists_is_case_insensitive_and_excludes_self(self):
        p = s.create_project(self.store, "Q3 Migration")
        self.assertTrue(s.name_exists(self.store, "q3 migration"))
        self.assertFalse(s.name_exists(self.store, "q3 migration", exclude_id=p["id"]))

    def test_index_order_is_most_recently_touched_first(self):
        a = s.create_project(self.store, "A")
        b = s.create_project(self.store, "B")
        a["touched_at"] = "2020-01-01T00:00:00Z"
        b["touched_at"] = "2021-01-01T00:00:00Z"
        self.assertEqual([p["name"] for p in s.projects(self.store)], ["B", "A"])


class EntryTest(StoreTestCase):
    def setUp(self):
        super().setUp()
        self.store, _ = s.load()
        self.p = s.create_project(self.store, "P")
        self.pid = self.p["id"]

    def _add(self, number, note=""):
        return s.add_entry(self.store, self.pid, "o/r", number, note)

    def _numbers(self):
        return [e["number"] for e in self.p["entries"]]

    def test_add_is_idempotent_and_never_overwrites_a_note(self):
        self.assertEqual(self._add(1, "original"), "added")
        self.assertEqual(self._add(1, "clobber"), "exists")
        self.assertEqual(self.p["entries"][0]["note"], "original")
        self.assertEqual(len(self.p["entries"]), 1)

    def test_add_bumps_touched_at(self):
        self.p["touched_at"] = ""
        self._add(1)
        self.assertNotEqual(self.p["touched_at"], "")

    def test_set_note_and_remove(self):
        self._add(1)
        s.set_note(self.store, self.pid, "o/r", 1, "  spaced  ")
        self.assertEqual(self.p["entries"][0]["note"], "spaced")
        removed = s.remove_entry(self.store, self.pid, "o/r", 1)
        self.assertEqual(removed["number"], 1)
        self.assertEqual(self._numbers(), [])

    def test_note_or_remove_on_a_missing_entry_raises(self):
        with self.assertRaises(s.StoreError):
            s.set_note(self.store, self.pid, "o/r", 99, "x")
        with self.assertRaises(s.StoreError):
            s.remove_entry(self.store, self.pid, "o/r", 99)

    def test_same_pr_in_two_projects_has_two_independent_notes(self):
        other = s.create_project(self.store, "Other")
        self._add(1, "here")
        s.add_entry(self.store, other["id"], "o/r", 1, "there")
        s.set_note(self.store, self.pid, "o/r", 1, "changed here")
        self.assertEqual(other["entries"][0]["note"], "there")

    def test_move_up_down_and_top(self):
        for n in (1, 2, 3):
            self._add(n)
        self.assertTrue(s.move_entry(self.store, self.pid, "o/r", 3, "up"))
        self.assertEqual(self._numbers(), [1, 3, 2])
        self.assertTrue(s.move_entry(self.store, self.pid, "o/r", 1, "down"))
        self.assertEqual(self._numbers(), [3, 1, 2])
        self.assertTrue(s.move_entry(self.store, self.pid, "o/r", 2, "top"))
        self.assertEqual(self._numbers(), [2, 3, 1])

    def test_move_at_the_ends_is_a_no_op(self):
        for n in (1, 2):
            self._add(n)
        self.assertFalse(s.move_entry(self.store, self.pid, "o/r", 1, "up"))
        self.assertFalse(s.move_entry(self.store, self.pid, "o/r", 2, "down"))
        self.assertFalse(s.move_entry(self.store, self.pid, "o/r", 1, "top"))
        self.assertEqual(self._numbers(), [1, 2])

    def test_move_jumps_past_hidden_entries(self):
        # Stored order 1 2 3 4 with 2 and 3 hidden (closed). Moving 4 up must
        # land it above 1 — one click, one visible move — rather than swapping
        # with a row nobody can see.
        for n in (1, 2, 3, 4):
            self._add(n)
        hidden = {2, 3}

        def visible(entry):
            return entry["number"] not in hidden

        self.assertTrue(s.move_entry(self.store, self.pid, "o/r", 4, "up", visible))
        self.assertEqual(self._numbers(), [4, 1, 2, 3])
        self.assertTrue(s.move_entry(self.store, self.pid, "o/r", 4, "down", visible))
        self.assertEqual(self._numbers(), [1, 2, 3, 4])

    def test_move_up_at_the_first_visible_row_is_a_no_op_even_with_hidden_above(self):
        for n in (1, 2):
            self._add(n)

        def visible(entry):
            return entry["number"] != 1

        self.assertFalse(s.move_entry(self.store, self.pid, "o/r", 2, "up", visible))
        self.assertEqual(self._numbers(), [1, 2])

    def test_unknown_direction_raises(self):
        self._add(1)
        with self.assertRaises(s.StoreError):
            s.move_entry(self.store, self.pid, "o/r", 1, "sideways")


class MembershipTest(StoreTestCase):
    def test_membership_maps_prs_to_every_project_they_are_in(self):
        store, _ = s.load()
        a = s.create_project(store, "A")
        b = s.create_project(store, "B")
        s.add_entry(store, a["id"], "o/r", 1)
        s.add_entry(store, b["id"], "o/r", 1)
        s.add_entry(store, b["id"], "o/r", 2)

        m = s.membership(store)
        self.assertEqual([p["name"] for p in m[("o/r", 1)]], ["A", "B"])
        self.assertEqual([p["name"] for p in m[("o/r", 2)]], ["B"])
        self.assertNotIn(("o/r", 3), m)

    def test_all_refs_is_deduped(self):
        store, _ = s.load()
        a = s.create_project(store, "A")
        b = s.create_project(store, "B")
        s.add_entry(store, a["id"], "o/r", 1)
        s.add_entry(store, b["id"], "o/r", 1)
        self.assertEqual(s.all_refs(store), [("o/r", 1)])


if __name__ == "__main__":
    unittest.main()
