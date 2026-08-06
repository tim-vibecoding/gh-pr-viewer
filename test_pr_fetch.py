import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pr_cache
import pr_core as c


def _pr_node(number):
    return {"number": number, "title": f"PR {number}", "state": "OPEN"}


def _completed(stdout):
    return subprocess.CompletedProcess([], 0, stdout=json.dumps(stdout), stderr="")


class FetchPrsByRefTest(unittest.TestCase):
    def test_returns_a_node_per_ref(self):
        payload = {"data": {"e0": {"pullRequest": _pr_node(1)},
                            "e1": {"pullRequest": _pr_node(2)}}}
        with mock.patch("subprocess.run", return_value=_completed(payload)):
            out = c.fetch_prs_by_ref([("o/r", 1), ("o/r2", 2)])
        self.assertEqual(out[("o/r", 1)]["number"], 1)
        self.assertEqual(out[("o/r2", 2)]["number"], 2)

    def test_one_bad_entry_does_not_take_down_the_rest(self):
        # A deleted repo or a lost grant comes back as a null alias *plus* an
        # `errors` array. fetch_prs would raise; here the other entries survive.
        payload = {
            "data": {"e0": {"pullRequest": _pr_node(1)}, "e1": None},
            "errors": [{"message": "Could not resolve to a Repository"}],
        }
        with mock.patch("subprocess.run", return_value=_completed(payload)):
            out = c.fetch_prs_by_ref([("o/r", 1), ("gone/repo", 2)])
        self.assertIsNotNone(out[("o/r", 1)])
        self.assertIsNone(out[("gone/repo", 2)])

    def test_a_missing_pr_in_a_live_repo_is_none(self):
        payload = {"data": {"e0": {"pullRequest": None}}}
        with mock.patch("subprocess.run", return_value=_completed(payload)):
            out = c.fetch_prs_by_ref([("o/r", 999)])
        self.assertIsNone(out[("o/r", 999)])

    def test_total_failure_raises(self):
        payload = {"data": None, "errors": [{"message": "Bad credentials"}]}
        with mock.patch("subprocess.run", return_value=_completed(payload)):
            with self.assertRaises(c.PRViewerError):
                c.fetch_prs_by_ref([("o/r", 1)])

    def test_partial_failure_survives_a_nonzero_exit(self):
        # `gh` exits non-zero on any errors payload, but the body is usable.
        payload = {"data": {"e0": {"pullRequest": _pr_node(1)}, "e1": None},
                   "errors": [{"message": "nope"}]}
        err = subprocess.CalledProcessError(1, [], output=json.dumps(payload), stderr="x")
        with mock.patch("subprocess.run", side_effect=err):
            out = c.fetch_prs_by_ref([("o/r", 1), ("gone/repo", 2)])
        self.assertIsNotNone(out[("o/r", 1)])

    def test_chunks_at_100_refs(self):
        refs = [("o/r", n) for n in range(150)]

        def fake_run(cmd, **kwargs):
            # Echo back a node for every alias the query asked for.
            aliases = [a.split("=")[0] for a in cmd if a.startswith("p")]
            data = {f"e{i}": {"pullRequest": _pr_node(i)} for i in range(len(aliases))}
            return _completed({"data": data})

        with mock.patch("subprocess.run", side_effect=fake_run) as run:
            out = c.fetch_prs_by_ref(refs)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(len(out), 150)

    def test_duplicate_refs_are_deduped(self):
        payload = {"data": {"e0": {"pullRequest": _pr_node(1)}}}
        with mock.patch("subprocess.run", return_value=_completed(payload)) as run:
            out = c.fetch_prs_by_ref([("o/r", 1), ("o/r", 1)])
        self.assertEqual(len(out), 1)
        self.assertEqual(run.call_count, 1)

    def test_repo_names_travel_as_variables_not_query_text(self):
        payload = {"data": {"e0": {"pullRequest": _pr_node(1)}}}
        with mock.patch("subprocess.run", return_value=_completed(payload)) as run:
            c.fetch_prs_by_ref([("khan/webapp", 42)])
        cmd = run.call_args[0][0]
        query = next(a for a in cmd if a.startswith("query="))
        self.assertNotIn("khan", query)
        self.assertIn("o0=khan", cmd)
        self.assertIn("n0=webapp", cmd)
        # -F, not -f: the number has to arrive typed as an Int.
        self.assertEqual(cmd[cmd.index("p0=42") - 1], "-F")

    def test_no_refs_makes_no_call(self):
        with mock.patch("subprocess.run") as run:
            self.assertEqual(c.fetch_prs_by_ref([]), {})
        run.assert_not_called()


class CachedFetchTest(unittest.TestCase):
    """The cache is off for every other test in the suite — these turn it on
    explicitly, pointed at a temp directory."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self._prev = os.environ.get("PR_VIEWER_CACHE")
        os.environ["PR_VIEWER_CACHE"] = str(Path(self._dir.name) / "cache")
        pr_cache.set_enabled(True)
        self.addCleanup(self._restore)

    def _restore(self):
        pr_cache.set_enabled(False)
        if self._prev is None:
            os.environ.pop("PR_VIEWER_CACHE", None)
        else:
            os.environ["PR_VIEWER_CACHE"] = self._prev
        self._dir.cleanup()

    def test_a_second_fetch_prs_does_not_shell_out(self):
        payload = {"data": {"viewer": {"login": "me",
                                       "pullRequests": {"nodes": [_pr_node(1)]}}}}
        with mock.patch("subprocess.run", return_value=_completed(payload)) as run:
            first = c.fetch_prs("@me")
            second = c.fetch_prs("@me")
        self.assertEqual(run.call_count, 1)
        self.assertEqual(first, second)

    def test_an_error_is_not_cached(self):
        err = subprocess.CalledProcessError(1, [], output="", stderr="Bad credentials")
        with mock.patch("subprocess.run", side_effect=err) as run:
            for _ in range(2):
                with self.assertRaises(c.PRViewerError):
                    c.fetch_prs("@me")
        self.assertEqual(run.call_count, 2)

    def test_two_users_do_not_share_an_entry(self):
        def fake_run(cmd, **kwargs):
            login = next((a.split("=", 1)[1] for a in cmd if a.startswith("login=")), "me")
            key = "user" if any(a.startswith("login=") for a in cmd) else "viewer"
            return _completed({"data": {key: {"login": login,
                                              "pullRequests": {"nodes": []}}}})

        with mock.patch("subprocess.run", side_effect=fake_run) as run:
            self.assertEqual(c.fetch_prs("@me")[0], "me")
            self.assertEqual(c.fetch_prs("someone")[0], "someone")
        self.assertEqual(run.call_count, 2)

    def test_a_warm_ref_is_dropped_from_the_query_and_still_returned(self):
        payload = {"data": {"e0": {"pullRequest": _pr_node(1)}}}
        with mock.patch("subprocess.run", return_value=_completed(payload)):
            c.fetch_prs_by_ref([("o/r", 1)])

        cold = {"data": {"e0": {"pullRequest": _pr_node(2)}}}
        with mock.patch("subprocess.run", return_value=_completed(cold)) as run:
            out = c.fetch_prs_by_ref([("o/r", 1), ("o/r2", 2)])
        # Only the cold ref is named in the query…
        cmd = run.call_args[0][0]
        self.assertIn("n0=r2", cmd)
        self.assertNotIn("n1=r2", cmd)
        # …and both come back, with `_children` attached either way.
        self.assertEqual(out[("o/r", 1)]["number"], 1)
        self.assertEqual(out[("o/r2", 2)]["number"], 2)
        self.assertEqual(out[("o/r", 1)]["_children"], [])

    def test_all_refs_warm_makes_no_call_at_all(self):
        payload = {"data": {"e0": {"pullRequest": _pr_node(1)}, "e1": None}}
        with mock.patch("subprocess.run", return_value=_completed(payload)):
            c.fetch_prs_by_ref([("o/r", 1), ("gone/repo", 2)])

        # A page needing only cached refs survives `gh` being broken.
        with mock.patch("subprocess.run", side_effect=AssertionError("shelled out")):
            out = c.fetch_prs_by_ref([("o/r", 1), ("gone/repo", 2)])
        self.assertEqual(out[("o/r", 1)]["number"], 1)
        self.assertIsNone(out[("gone/repo", 2)])   # the miss was cached too

    def test_a_hand_edited_entry_is_refetched_rather_than_crashing(self):
        pr_cache.put("prs/@me", "not a (login, nodes) pair")
        pr_cache.put("ref/o/r#1", "not a pr node")

        prs = {"data": {"viewer": {"login": "me", "pullRequests": {"nodes": []}}}}
        with mock.patch("subprocess.run", return_value=_completed(prs)):
            self.assertEqual(c.fetch_prs("@me"), ("me", []))
        ref = {"data": {"e0": {"pullRequest": _pr_node(1)}}}
        with mock.patch("subprocess.run", return_value=_completed(ref)):
            self.assertEqual(c.fetch_prs_by_ref([("o/r", 1)])[("o/r", 1)]["number"], 1)

    def test_refresh_refetches_a_warm_entry_and_rewrites_it(self):
        """The warmer's mode: a plain call would be satisfied by the entry
        that's about to expire, and so would write nothing."""
        first = {"data": {"viewer": {"login": "me",
                                     "pullRequests": {"nodes": [_pr_node(1)]}}}}
        with mock.patch("subprocess.run", return_value=_completed(first)):
            c.fetch_prs("@me")

        second = {"data": {"viewer": {"login": "me",
                                      "pullRequests": {"nodes": [_pr_node(2)]}}}}
        with mock.patch("subprocess.run", return_value=_completed(second)) as run:
            _login, nodes = c.fetch_prs("@me", refresh=True)
        self.assertEqual(run.call_count, 1)
        self.assertEqual([n["number"] for n in nodes], [2])
        # …and the newly fetched value is what a later page sees.
        with mock.patch("subprocess.run", side_effect=AssertionError("shelled out")):
            self.assertEqual([n["number"] for n in c.fetch_prs("@me")[1]], [2])

    def test_refresh_refetches_every_ref_even_when_all_are_warm(self):
        payload = {"data": {"e0": {"pullRequest": _pr_node(1)}, "e1": None}}
        with mock.patch("subprocess.run", return_value=_completed(payload)):
            c.fetch_prs_by_ref([("o/r", 1), ("gone/repo", 2)])

        fresh = {"data": {"e0": {"pullRequest": _pr_node(1)},
                          "e1": {"pullRequest": _pr_node(2)}}}
        with mock.patch("subprocess.run", return_value=_completed(fresh)) as run:
            out = c.fetch_prs_by_ref([("o/r", 1), ("gone/repo", 2)], refresh=True)
        # Both refs are named in the query — nothing was dropped as a hit.
        cmd = run.call_args[0][0]
        self.assertIn("n0=r", cmd)
        self.assertIn("n1=repo", cmd)
        self.assertEqual(out[("gone/repo", 2)]["number"], 2)
        # A ref that came back this time replaces the cached `null`.
        with mock.patch("subprocess.run", side_effect=AssertionError("shelled out")):
            again = c.fetch_prs_by_ref([("gone/repo", 2)])
        self.assertEqual(again[("gone/repo", 2)]["number"], 2)

    def test_each_hit_gets_its_own_object_graph(self):
        payload = {"data": {"e0": {"pullRequest": _pr_node(1)}}}
        with mock.patch("subprocess.run", return_value=_completed(payload)):
            c.fetch_prs_by_ref([("o/r", 1)])
        with mock.patch("subprocess.run", side_effect=AssertionError("shelled out")):
            first = c.fetch_prs_by_ref([("o/r", 1)])[("o/r", 1)]
            second = c.fetch_prs_by_ref([("o/r", 1)])[("o/r", 1)]
        # `build_forest` mutates nodes in place, so one render's tree must not
        # be able to leak into the next.
        first["_children"].append("leak")
        self.assertEqual(second["_children"], [])


class PrIsOpenTest(unittest.TestCase):
    def test_states(self):
        self.assertTrue(c.pr_is_open({"state": "OPEN"}))
        self.assertFalse(c.pr_is_open({"state": "MERGED"}))
        self.assertFalse(c.pr_is_open({"state": "CLOSED"}))
        # The home-page query predates `state`; absent means open.
        self.assertTrue(c.pr_is_open({}))


if __name__ == "__main__":
    unittest.main()
