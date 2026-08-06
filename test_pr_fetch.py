import json
import subprocess
import unittest
from unittest import mock

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


class PrIsOpenTest(unittest.TestCase):
    def test_states(self):
        self.assertTrue(c.pr_is_open({"state": "OPEN"}))
        self.assertFalse(c.pr_is_open({"state": "MERGED"}))
        self.assertFalse(c.pr_is_open({"state": "CLOSED"}))
        # The home-page query predates `state`; absent means open.
        self.assertTrue(c.pr_is_open({}))


if __name__ == "__main__":
    unittest.main()
