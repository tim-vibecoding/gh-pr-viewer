import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import pr_cache


class CacheTestCase(unittest.TestCase):
    """The only module that enables the cache, pointed at a temp directory."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.dir = Path(self._dir.name) / "cache"
        self._prev = os.environ.get("PR_VIEWER_CACHE")
        os.environ["PR_VIEWER_CACHE"] = str(self.dir)
        pr_cache.set_enabled(True)
        self.addCleanup(self._restore)

    def _restore(self):
        pr_cache.set_enabled(False)
        if self._prev is None:
            os.environ.pop("PR_VIEWER_CACHE", None)
        else:
            os.environ["PR_VIEWER_CACHE"] = self._prev
        self._dir.cleanup()

    def entry_path(self, key):
        return pr_cache._path(key)

    def rewrite(self, key, **changes):
        path = self.entry_path(key)
        entry = json.loads(path.read_text(encoding="utf-8"))
        entry.update(changes)
        path.write_text(json.dumps(entry), encoding="utf-8")


class GetPutTest(CacheTestCase):
    def test_a_value_round_trips_inside_the_ttl(self):
        pr_cache.put("prs/@me", ["me", [{"number": 1}]])
        self.assertEqual(pr_cache.get("prs/@me"), (True, ["me", [{"number": 1}]]))

    def test_an_absent_key_is_a_miss(self):
        self.assertEqual(pr_cache.get("prs/nobody"), (False, None))

    def test_an_entry_past_the_ttl_is_a_miss(self):
        pr_cache.put("prs/@me", ["me", []])
        self.rewrite("prs/@me", fetched_at=time.time() - pr_cache.ttl() - 1)
        self.assertEqual(pr_cache.get("prs/@me"), (False, None))

    def test_none_round_trips_as_a_hit(self):
        # The negative-caching contract: an inaccessible PR is a real cached
        # value, so one dead entry doesn't re-cost a round trip every render.
        pr_cache.put("ref/gone/repo#2", None)
        self.assertEqual(pr_cache.get("ref/gone/repo#2"), (True, None))

    def test_garbage_a_wrong_version_and_a_future_stamp_are_all_misses(self):
        pr_cache.put("k", "v")
        path = self.entry_path("k")

        path.write_text("{ not json", encoding="utf-8")
        self.assertEqual(pr_cache.get("k"), (False, None))

        path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        self.assertEqual(pr_cache.get("k"), (False, None))

        pr_cache.put("k", "v")
        self.rewrite("k", version=pr_cache.VERSION + 1)
        self.assertEqual(pr_cache.get("k"), (False, None))

        # A clock jump forward would otherwise make an entry immortal.
        pr_cache.put("k", "v")
        self.rewrite("k", fetched_at=time.time() + 10_000)
        self.assertEqual(pr_cache.get("k"), (False, None))

        pr_cache.put("k", "v")
        self.rewrite("k", fetched_at="soon")
        self.assertEqual(pr_cache.get("k"), (False, None))

    def test_the_key_is_stored_so_the_directory_stays_greppable(self):
        pr_cache.put("ref/o/r#7", {"number": 7})
        entry = json.loads(self.entry_path("ref/o/r#7").read_text(encoding="utf-8"))
        self.assertEqual(entry["key"], "ref/o/r#7")

    def test_an_unwritable_directory_is_not_an_error(self):
        self.dir.mkdir(parents=True)
        os.chmod(self.dir, 0o500)
        self.addCleanup(os.chmod, self.dir, 0o700)
        pr_cache.put("k", "v")                      # must not raise
        self.assertEqual(pr_cache.get("k"), (False, None))

    def test_an_unserializable_value_leaves_no_temp_file_behind(self):
        pr_cache.put("k", {1, 2, 3})                # a set isn't JSON
        self.assertEqual(pr_cache.get("k"), (False, None))
        self.assertEqual(list(self.dir.glob("*")) if self.dir.exists() else [], [])

    def test_a_ttl_from_the_environment_wins(self):
        os.environ["PR_VIEWER_CACHE_TTL"] = "0"
        self.addCleanup(os.environ.pop, "PR_VIEWER_CACHE_TTL", None)
        pr_cache.put("k", "v")
        self.assertEqual(pr_cache.get("k"), (False, None))

        os.environ["PR_VIEWER_CACHE_TTL"] = "not a number"
        self.assertEqual(pr_cache.ttl(), pr_cache.DEFAULT_TTL_SECONDS)


class ClearTest(CacheTestCase):
    def test_clear_removes_our_files_and_leaves_anything_else_alone(self):
        pr_cache.put("a", 1)
        pr_cache.put("b", 2)
        (self.dir / "x.tmp-999").write_text("half written", encoding="utf-8")
        stranger = self.dir / "NOTES.txt"
        stranger.write_text("not ours", encoding="utf-8")

        self.assertEqual(pr_cache.clear(), 3)
        self.assertEqual(pr_cache.get("a"), (False, None))
        self.assertTrue(stranger.exists())

    def test_clearing_a_directory_that_never_existed_is_fine(self):
        self.assertEqual(pr_cache.clear(), 0)


class DisabledTest(CacheTestCase):
    def setUp(self):
        super().setUp()
        pr_cache.set_enabled(False)

    def test_put_writes_nothing_and_get_always_misses(self):
        pr_cache.put("k", "v")
        self.assertFalse(self.dir.exists())
        self.assertEqual(pr_cache.get("k"), (False, None))

    def test_an_entry_written_while_enabled_is_ignored(self):
        pr_cache.set_enabled(True)
        pr_cache.put("k", "v")
        pr_cache.set_enabled(False)
        self.assertEqual(pr_cache.get("k"), (False, None))


if __name__ == "__main__":
    unittest.main()
