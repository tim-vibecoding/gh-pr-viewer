import os
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import HTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import urlencode, urlparse, parse_qs

import pr_core
import pr_projects
import pr_server
import pr_store
from test_pr_projects import make_pr


class RedirectUrlTest(unittest.TestCase):
    def test_flash_args_are_merged_into_the_return_path(self):
        out = pr_server.redirect_url("/projects/x?closed=show", "/projects",
                                     anchor="pr-o-r-1", flash="added", pr=4821)
        parsed = urlparse(out)
        self.assertEqual(parsed.path, "/projects/x")
        self.assertEqual(parse_qs(parsed.query)["closed"], ["show"])
        self.assertEqual(parse_qs(parsed.query)["flash"], ["added"])
        self.assertEqual(parsed.fragment, "pr-o-r-1")

    def test_an_old_flash_is_replaced_not_stacked(self):
        out = pr_server.redirect_url("/projects/x?flash=moved&pr=1", "/projects",
                                     flash="noted", pr=2)
        self.assertEqual(parse_qs(urlparse(out).query)["flash"], ["noted"])
        self.assertEqual(parse_qs(urlparse(out).query)["pr"], ["2"])

    def test_offsite_return_to_falls_back(self):
        for evil in ("https://evil.example/x", "//evil.example/x", "javascript:x",
                     "", None, "not-a-path"):
            self.assertEqual(
                urlparse(pr_server.redirect_url(evil, "/projects")).path, "/projects",
                evil,
            )

    def test_repeated_flash_args_survive(self):
        out = pr_server.redirect_url("/", "/", flash="added", project=["a", "b"])
        self.assertEqual(parse_qs(urlparse(out).query)["project"], ["a", "b"])


class HandlerTestCase(unittest.TestCase):
    """POST handlers against a temp store, with GitHub patched out."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "projects.json"
        self._prev = os.environ.get("PR_VIEWER_STORE")
        os.environ["PR_VIEWER_STORE"] = str(self.path)
        self.addCleanup(self._restore)

        self.prs = {}
        patcher = mock.patch.object(
            pr_core, "fetch_prs_by_ref", side_effect=self._fetch_by_ref
        )
        self.fetch_by_ref = patcher.start()
        self.addCleanup(patcher.stop)

    def _fetch_by_ref(self, refs):
        return {ref: self.prs.get(ref) for ref in refs}

    def _restore(self):
        if self._prev is None:
            os.environ.pop("PR_VIEWER_STORE", None)
        else:
            os.environ["PR_VIEWER_STORE"] = self._prev
        self._dir.cleanup()

    # -- helpers -----------------------------------------------------------

    def store(self):
        store, error = pr_store.load()
        self.assertIsNone(error)
        return store

    def make_project(self, name="P", entries=()):
        store = self.store()
        project = pr_store.create_project(store, name)
        for repo, number, note in entries:
            pr_store.add_entry(store, project["id"], repo, number, note)
            self.prs[(repo, number)] = make_pr(number, repo=repo)
        pr_store.save(store)
        return project["id"]

    def project(self, project_id):
        return pr_store.get(self.store(), project_id)

    def flash_of(self, location):
        return parse_qs(urlparse(location).query).get("flash", [None])[0]


class CreateTest(HandlerTestCase):
    def test_create_lands_on_the_new_project(self):
        location = pr_server.post_create({"name": ["Q3 migration"],
                                          "description": ["why"]})
        self.assertEqual(self.flash_of(location), "created")
        project = pr_store.projects(self.store())[0]
        self.assertEqual(project["name"], "Q3 migration")
        self.assertEqual(project["description"], "why")
        self.assertTrue(urlparse(location).path.endswith(project["id"]))

    def test_a_blank_name_is_refused_without_writing(self):
        location = pr_server.post_create({"name": ["   "]})
        self.assertEqual(self.flash_of(location), "noname")
        self.assertEqual(self.store()["projects"], [])

    def test_a_duplicate_name_warns_but_does_not_block(self):
        self.make_project("Q3 migration")
        form = {"name": ["Q3 migration"]}
        location = pr_server.post_create(form)
        self.assertEqual(self.flash_of(location), "dupname")
        self.assertEqual(len(self.store()["projects"]), 1)

        form["confirm"] = ["1"]
        location = pr_server.post_create(form)
        self.assertEqual(self.flash_of(location), "created")
        self.assertEqual(len(self.store()["projects"]), 2)


class EntryTest(HandlerTestCase):
    def test_add_pr_by_url(self):
        pid = self.make_project()
        self.prs[("khan/webapp", 4821)] = make_pr(4821, repo="khan/webapp")
        location = pr_server.post_add_pr({
            "project_id": [pid], "ref": ["https://github.com/khan/webapp/pull/4821"],
            "note": ["has to land first"], "return_to": [f"/projects/{pid}?closed=hide"],
        })
        self.assertEqual(self.flash_of(location), "added")
        self.assertEqual(urlparse(location).fragment, "pr-khan-webapp-4821")
        entries = self.project(pid)["entries"]
        self.assertEqual(entries, [{"repo": "khan/webapp", "number": 4821,
                                    "note": "has to land first"}])

    def test_unparseable_input_keeps_the_typed_text(self):
        pid = self.make_project()
        location = pr_server.post_add_pr({"project_id": [pid], "ref": ["nonsense"]})
        self.assertEqual(self.flash_of(location), "badref")
        self.assertEqual(parse_qs(urlparse(location).query)["text"], ["nonsense"])
        self.assertEqual(self.project(pid)["entries"], [])

    def test_an_unfetchable_pr_is_not_added(self):
        pid = self.make_project()
        location = pr_server.post_add_pr({"project_id": [pid],
                                          "ref": ["ghost/repo#1"]})
        self.assertEqual(self.flash_of(location), "badref")
        self.assertEqual(self.project(pid)["entries"], [])

    def test_a_github_outage_says_so_rather_than_blaming_the_url(self):
        pid = self.make_project()
        self.fetch_by_ref.side_effect = pr_core.PRViewerError("gh is down")
        location = pr_server.post_add_pr({"project_id": [pid], "ref": ["o/r#1"]})
        self.assertEqual(self.flash_of(location), "fetchfail")
        self.assertEqual(parse_qs(urlparse(location).query)["text"], ["o/r#1"])

    def test_adding_twice_is_not_an_error_and_keeps_the_note(self):
        pid = self.make_project(entries=[("o/r", 1, "original")])
        location = pr_server.post_add_pr({"project_id": [pid], "ref": ["o/r#1"],
                                          "note": ["clobber"]})
        self.assertEqual(self.flash_of(location), "exists")
        self.assertEqual(urlparse(location).fragment, "pr-o-r-1")
        self.assertEqual(self.project(pid)["entries"][0]["note"], "original")

    def test_note_is_saved(self):
        pid = self.make_project(entries=[("o/r", 1, "")])
        location = pr_server.post_entry_note({
            "project_id": [pid], "repo": ["o/r"], "number": ["1"],
            "note": ["because Sam has context"],
        })
        self.assertEqual(self.flash_of(location), "noted")
        self.assertEqual(self.project(pid)["entries"][0]["note"],
                         "because Sam has context")

    def test_remove_takes_the_entry_out(self):
        pid = self.make_project(entries=[("o/r", 1, "note"), ("o/r", 2, "")])
        location = pr_server.post_entry_remove({
            "project_id": [pid], "repo": ["o/r"], "number": ["1"],
        })
        self.assertEqual(self.flash_of(location), "removed")
        self.assertEqual([e["number"] for e in self.project(pid)["entries"]], [2])

    def test_move_reorders_and_anchors_to_the_row(self):
        pid = self.make_project(entries=[("o/r", 1, ""), ("o/r", 2, "")])
        location = pr_server.post_entry_move({
            "project_id": [pid], "repo": ["o/r"], "number": ["2"],
            "direction": ["up"], "closed": ["hide"],
        })
        self.assertEqual(self.flash_of(location), "moved")
        self.assertEqual(urlparse(location).fragment, "pr-o-r-2")
        self.assertEqual([e["number"] for e in self.project(pid)["entries"]], [2, 1])

    def test_a_move_at_the_end_is_a_quiet_no_op(self):
        pid = self.make_project(entries=[("o/r", 1, ""), ("o/r", 2, "")])
        location = pr_server.post_entry_move({
            "project_id": [pid], "repo": ["o/r"], "number": ["1"],
            "direction": ["up"], "closed": ["hide"],
        })
        self.assertIsNone(self.flash_of(location))
        self.assertEqual([e["number"] for e in self.project(pid)["entries"]], [1, 2])

    def test_move_uses_the_same_visible_list_the_page_showed(self):
        # 1 open, 2 merged (hidden), 3 open. Moving 3 up must jump the hidden
        # entry and land above 1.
        pid = self.make_project(entries=[("o/r", 1, ""), ("o/r", 2, ""), ("o/r", 3, "")])
        self.prs[("o/r", 2)] = make_pr(2, state="MERGED")
        pr_server.post_entry_move({
            "project_id": [pid], "repo": ["o/r"], "number": ["3"],
            "direction": ["up"], "closed": ["hide"],
        })
        self.assertEqual([e["number"] for e in self.project(pid)["entries"]], [3, 1, 2])

    def test_acting_on_a_deleted_project_says_gone_rather_than_crashing(self):
        for handler, form in [
            (pr_server.post_entry_note, {"project_id": ["nope"], "repo": ["o/r"],
                                         "number": ["1"], "note": ["x"]}),
            (pr_server.post_entry_remove, {"project_id": ["nope"], "repo": ["o/r"],
                                           "number": ["1"]}),
            (pr_server.post_entry_move, {"project_id": ["nope"], "repo": ["o/r"],
                                         "number": ["1"], "direction": ["up"]}),
            (pr_server.post_edit, {"project_id": ["nope"], "name": ["x"]}),
            (pr_server.post_delete, {"project_id": ["nope"]}),
        ]:
            self.assertEqual(self.flash_of(handler(form)), "gone", handler.__name__)


class AddToProjectsTest(HandlerTestCase):
    def test_adds_to_several_projects_at_once(self):
        a = self.make_project("A")
        b = self.make_project("B")
        location = pr_server.post_add_to_projects({
            "repo": ["o/r"], "number": ["7"], "project_id": [a, b],
            "note": ["triage"], "return_to": ["/?filter=uncategorized"],
        })
        self.assertEqual(self.flash_of(location), "added")
        self.assertEqual(parse_qs(urlparse(location).query)["project"], [a, b])
        for pid in (a, b):
            self.assertEqual(self.project(pid)["entries"][0]["note"], "triage")

    def test_a_new_project_name_creates_and_adds_in_one_action(self):
        location = pr_server.post_add_to_projects({
            "repo": ["o/r"], "number": ["7"], "new_project_name": ["Fresh"],
        })
        self.assertEqual(self.flash_of(location), "added")
        project = pr_store.projects(self.store())[0]
        self.assertEqual(project["name"], "Fresh")
        self.assertEqual(project["entries"][0]["number"], 7)

    def test_next_anchor_wins_so_a_run_of_adds_walks_down_the_list(self):
        a = self.make_project("A")
        location = pr_server.post_add_to_projects({
            "repo": ["o/r"], "number": ["7"], "project_id": [a],
            "next_anchor": ["pr-o-r-9"], "return_to": ["/?filter=uncategorized"],
        })
        self.assertEqual(urlparse(location).fragment, "pr-o-r-9")

    def test_no_targets_is_refused(self):
        location = pr_server.post_add_to_projects({"repo": ["o/r"], "number": ["7"]})
        self.assertEqual(self.flash_of(location), "error")


class CorruptStoreTest(HandlerTestCase):
    def test_mutations_refuse_and_do_not_overwrite(self):
        self.path.write_text("{ broken", encoding="utf-8")
        for handler, form in [
            (pr_server.post_create, {"name": ["X"]}),
            (pr_server.post_edit, {"project_id": ["x"], "name": ["X"]}),
            (pr_server.post_delete, {"project_id": ["x"]}),
            (pr_server.post_add_pr, {"project_id": ["x"], "ref": ["o/r#1"]}),
            (pr_server.post_entry_note, {"project_id": ["x"], "repo": ["o/r"],
                                         "number": ["1"], "note": ["n"]}),
            (pr_server.post_entry_move, {"project_id": ["x"], "repo": ["o/r"],
                                         "number": ["1"], "direction": ["up"]}),
            (pr_server.post_entry_remove, {"project_id": ["x"], "repo": ["o/r"],
                                           "number": ["1"]}),
            (pr_server.post_add_to_projects, {"repo": ["o/r"], "number": ["1"],
                                              "project_id": ["x"]}),
        ]:
            self.assertEqual(self.flash_of(handler(form)), "unreadable",
                             handler.__name__)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{ broken")


class RecoveryTest(HandlerTestCase):
    def test_a_damaged_store_can_be_set_aside_without_losing_it(self):
        self.path.write_text("{ broken", encoding="utf-8")
        location = pr_server.post_recover({})
        self.assertEqual(self.flash_of(location), "recovered")
        self.assertEqual(self.store()["projects"], [])
        kept = self.path.with_name(self.path.name + ".corrupt-1")
        self.assertEqual(kept.read_text(encoding="utf-8"), "{ broken")

    def test_repeated_recoveries_do_not_overwrite_each_other(self):
        for n in (1, 2):
            self.path.write_text(f"broken {n}", encoding="utf-8")
            pr_server.post_recover({})
            kept = self.path.with_name(f"{self.path.name}.corrupt-{n}")
            self.assertEqual(kept.read_text(encoding="utf-8"), f"broken {n}")

    def test_a_healthy_store_is_never_set_aside(self):
        pid = self.make_project("Keep me")
        location = pr_server.post_recover({})
        self.assertIsNone(self.flash_of(location))
        self.assertIsNotNone(self.project(pid))

    def test_a_newer_version_store_is_not_treated_as_damage(self):
        # It's readable, just not writable by us — starting over would throw
        # away someone's real data.
        self.path.write_text(
            '{"version": 99, "projects": [{"id": "a", "name": "N", "entries": []}]}',
            encoding="utf-8",
        )
        self.assertFalse(pr_store.needs_recovery())
        pr_server.post_recover({})
        self.assertIn('"version": 99', self.path.read_text(encoding="utf-8"))

    def test_the_index_offers_recovery_only_for_real_damage(self):
        self.path.write_text("{ broken", encoding="utf-8")
        store, error = pr_store.load()
        self.assertIn("Start a new projects file",
                      pr_projects.render_index(store, error, {}, True, None, {}))
        self.path.write_text('{"version": 1, "projects": []}', encoding="utf-8")
        self.assertNotIn("Start a new projects file",
                         pr_projects.render_index(store, "some other error",
                                                  {}, True, None, {}))


class LiveServerTest(HandlerTestCase):
    """A real HTTPServer on port 0, with both GitHub fetches patched."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(pr_core, "fetch_prs", return_value=("me", []))
        self.fetch_prs = patcher.start()
        self.addCleanup(patcher.stop)

        self.httpd = HTTPServer(("127.0.0.1", 0), pr_server._make_handler("@me"))
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)
        self.host = f"127.0.0.1:{self.httpd.server_address[1]}"

    def request(self, method, path, body=None, headers=None):
        conn = HTTPConnection(self.host, timeout=10)
        head = dict(headers or {})
        if body is not None:
            head.setdefault("Content-Type", "application/x-www-form-urlencoded")
            head.setdefault("Sec-Fetch-Site", "same-origin")
        conn.request(method, path, body=body, headers=head)
        response = conn.getresponse()
        payload = response.read().decode("utf-8")
        conn.close()
        return response.status, response.getheader("Location"), payload

    def post(self, path, fields, headers=None):
        return self.request("POST", path, urlencode(fields, doseq=True), headers)

    def test_get_routes(self):
        pid = self.make_project("Q3", entries=[("o/r", 1, "n")])
        for path, expect in [
            ("/", "Open PRs"),
            ("/projects", "Q3"),
            (f"/projects/{pid}", "Q3"),
            (f"/projects/{pid}/delete", "This removes 1 PR"),
        ]:
            status, _location, body = self.request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertIn(expect, body, path)

    def test_unknown_routes_are_404(self):
        for path in ("/nope", "/projects/does-not-exist", "/projects/x/delete"):
            status, _l, _b = self.request("GET", path)
            self.assertEqual(status, 404, path)
        self.assertEqual(self.post("/project/nope", {})[0], 404)

    def test_every_mutation_answers_303(self):
        pid = self.make_project("P", entries=[("o/r", 1, "note")])
        for path, fields in [
            ("/project/create", {"name": "New"}),
            ("/project/edit", {"project_id": pid, "name": "Renamed"}),
            ("/project/entry/note", {"project_id": pid, "repo": "o/r",
                                     "number": "1", "note": "x"}),
            ("/project/entry/move", {"project_id": pid, "repo": "o/r",
                                     "number": "1", "direction": "up"}),
            ("/project/entry/remove", {"project_id": pid, "repo": "o/r",
                                       "number": "1"}),
            ("/project/delete", {"project_id": pid}),
        ]:
            status, location, _b = self.post(path, fields)
            self.assertEqual(status, 303, path)
            self.assertTrue(location.startswith("/"), (path, location))

    def test_a_cross_origin_post_is_rejected(self):
        pid = self.make_project("P")
        for headers in (
            {"Sec-Fetch-Site": "cross-site"},
            {"Origin": "https://evil.example"},
        ):
            status, _l, _b = self.post("/project/delete", {"project_id": pid}, headers)
            self.assertEqual(status, 403, headers)
        # …and nothing was deleted.
        self.assertIsNotNone(self.project(pid))

    def test_an_oversized_body_is_refused(self):
        status, _l, _b = self.request(
            "POST", "/project/create", "name=" + "x" * (pr_server.MAX_BODY + 1),
            {"Content-Type": "application/x-www-form-urlencoded",
             "Sec-Fetch-Site": "same-origin"},
        )
        self.assertEqual(status, 413)

    def test_a_store_error_redirects_with_a_flash_and_leaves_the_server_up(self):
        self.path.write_text("{ broken", encoding="utf-8")
        status, location, _b = self.post("/project/create", {"name": "X"})
        self.assertEqual(status, 303)
        self.assertIn("flash=unreadable", location)
        self.assertEqual(self.request("GET", "/")[0], 200)

    def test_an_explicit_closed_param_is_remembered(self):
        pid = self.make_project("P", entries=[("o/r", 1, "")])
        self.request("GET", f"/projects/{pid}?closed=show")
        self.assertTrue(self.project(pid)["show_closed"])
        # …and drives the page on a later visit with no parameter.
        _s, _l, body = self.request("GET", f"/projects/{pid}")
        self.assertIn('href="/projects/%s?closed=show" aria-current="page"' % pid, body)

    def test_a_github_failure_on_the_detail_page_still_renders_the_entries(self):
        pid = self.make_project("P", entries=[("o/r", 1, "my note")])
        self.fetch_by_ref.side_effect = pr_core.PRViewerError("gh exploded")
        status, _l, body = self.request("GET", f"/projects/{pid}")
        self.assertEqual(status, 200)
        self.assertIn("my note", body)
        self.assertIn("gh exploded", body)

    def test_favicon_is_a_quiet_204(self):
        self.assertEqual(self.request("GET", "/favicon.ico")[0], 204)


if __name__ == "__main__":
    unittest.main()
