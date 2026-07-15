import unittest

import pr_core as c


def _check_run(name, status, conclusion=None, ts="2020-01-01T00:00:00Z", workflow=None):
    node = {
        "__typename": "CheckRun",
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "completedAt": ts,
    }
    if workflow is not None:
        node["checkSuite"] = {"workflowRun": {"workflow": {"name": workflow}}}
    return node


# The reviewer runs as jobs (named `agent`, `activation`, …) under a workflow
# named "PR Reviewer" — the job name itself gives no hint.
def _reviewer_run(job, status, conclusion=None, ts="2020-01-01T00:00:00Z"):
    return _check_run(job, status, conclusion, ts, workflow="PR Reviewer")


def _rollup(*nodes):
    return {"statusCheckRollup": {"contexts": {"nodes": list(nodes)}}}


class BucketChecksTest(unittest.TestCase):
    def test_reviewer_checks_excluded_from_main(self):
        pr = _rollup(
            _check_run("Build", "COMPLETED", "SUCCESS"),
            _reviewer_run("agent", "COMPLETED", "FAILURE"),
            _reviewer_run("activation", "IN_PROGRESS"),
        )
        buckets = c.bucket_checks(pr)
        # Only the Build check lands in Main; the reviewer jobs are gone, so
        # Main stays a clean 1/1 success rather than failing or pending.
        self.assertEqual(
            buckets["other"], {"state": "success", "passed": 1, "total": 1}
        )
        self.assertEqual(buckets["e2e"], {"state": "none", "passed": 0, "total": 0})

    def test_only_reviewer_checks_leaves_main_empty(self):
        pr = _rollup(_reviewer_run("agent", "IN_PROGRESS"))
        self.assertEqual(
            c.bucket_checks(pr)["other"],
            {"state": "none", "passed": 0, "total": 0},
        )

    def test_job_named_pr_reviewer_without_workflow_stays_in_main(self):
        # Detection keys off the workflow, not the job name: a plain check whose
        # own name happens to start with "PR Reviewer" is not pulled out.
        pr = _rollup(_check_run("PR Reviewer helper", "COMPLETED", "SUCCESS"))
        self.assertEqual(
            c.bucket_checks(pr)["other"],
            {"state": "success", "passed": 1, "total": 1},
        )


class ReviewerCheckStateTest(unittest.TestCase):
    def test_none_when_no_rollup(self):
        self.assertIsNone(c.reviewer_check_state({}))
        self.assertIsNone(c.reviewer_check_state({"statusCheckRollup": None}))

    def test_none_when_no_reviewer_checks(self):
        pr = _rollup(_check_run("Build", "COMPLETED", "SUCCESS"))
        self.assertIsNone(c.reviewer_check_state(pr))

    def test_pending(self):
        pr = _rollup(_reviewer_run("agent", "IN_PROGRESS"))
        self.assertEqual(c.reviewer_check_state(pr), "pending")

    def test_errored(self):
        pr = _rollup(_reviewer_run("agent", "COMPLETED", "FAILURE"))
        self.assertEqual(c.reviewer_check_state(pr), "errored")

    def test_success(self):
        pr = _rollup(_reviewer_run("agent", "COMPLETED", "SUCCESS"))
        self.assertEqual(c.reviewer_check_state(pr), "success")

    def test_pending_wins_over_failure(self):
        # A still-running job should read as "working", not flash an error.
        pr = _rollup(
            _reviewer_run("agent", "COMPLETED", "FAILURE"),
            _reviewer_run("activation", "IN_PROGRESS"),
        )
        self.assertEqual(c.reviewer_check_state(pr), "pending")

    def test_failure_wins_over_success(self):
        pr = _rollup(
            _reviewer_run("agent", "COMPLETED", "SUCCESS"),
            _reviewer_run("activation", "COMPLETED", "FAILURE"),
        )
        self.assertEqual(c.reviewer_check_state(pr), "errored")

    def test_cancelled_run_ignored(self):
        # A stale CANCELLED run normalizes to "skipped" and is dropped; with a
        # newer superseding success the collapsed state is success.
        pr = _rollup(
            _reviewer_run("agent", "COMPLETED", "CANCELLED", ts="2020-01-01T00:00:00Z"),
            _reviewer_run("agent", "COMPLETED", "SUCCESS", ts="2020-01-02T00:00:00Z"),
        )
        self.assertEqual(c.reviewer_check_state(pr), "success")

    def test_all_skipped_is_none(self):
        pr = _rollup(_reviewer_run("agent", "COMPLETED", "SKIPPED"))
        self.assertIsNone(c.reviewer_check_state(pr))


class RenderReviewerPillTest(unittest.TestCase):
    def test_pending_pill(self):
        html = c.render_reviewer_pill("pending")
        self.assertIn("pill bot pending", html)
        self.assertIn(c.CHECK_GLYPH["pending"], html)
        self.assertIn("PR Reviewer running", html)

    def test_errored_pill_is_distinct_from_changes(self):
        html = c.render_reviewer_pill("errored")
        # Dedicated `errored` class, not the red `changes` styling, and the ⚠
        # glyph rather than the ✗ used by a CHANGES_REQUESTED review.
        self.assertIn("pill bot errored", html)
        self.assertNotIn("changes", html)
        self.assertIn("⚠", html)
        self.assertNotIn(c.CHECK_GLYPH["failure"], html)

    def test_success_renders_nothing(self):
        # The verdict comes through the bot review pill, not the check.
        self.assertEqual(c.render_reviewer_pill("success"), "")

    def test_neutral_pill(self):
        self.assertIn("pill bot neutral", c.render_reviewer_pill("neutral"))


def _pr(rollup_nodes, bot_state):
    return {
        "number": 1, "title": "T", "url": "http://x", "isDraft": False,
        "author": {"login": "me"}, "baseRefName": "main", "headRefName": "f",
        "repository": {"nameWithOwner": "o/r", "defaultBranchRef": {"name": "main"}},
        "labels": {"nodes": []}, "reviewRequests": {"nodes": []},
        "reviewDecision": None, "_children": [],
        "reviews": {"nodes": [
            {"author": {"__typename": "Bot", "login": "github-actions"},
             "state": bot_state, "submittedAt": "2020-01-01T00:00:00Z"},
        ]},
        "statusCheckRollup": {"contexts": {"nodes": rollup_nodes}},
    }


class RenderPrPillCombinationTest(unittest.TestCase):
    def test_pending_check_hides_stale_review_pill(self):
        # Reviewer check running while a prior CHANGES_REQUESTED review lingers:
        # show only the pending reviewer pill, not the stale red review pill.
        html = c.render_pr(
            _pr([_reviewer_run("agent", "IN_PROGRESS")], "CHANGES_REQUESTED"),
            is_root=True,
        )
        self.assertIn("pill bot pending", html)
        self.assertNotIn("pill bot changes", html)

    def test_errored_check_hides_review_pill(self):
        html = c.render_pr(
            _pr([_reviewer_run("agent", "COMPLETED", "FAILURE")], "CHANGES_REQUESTED"),
            is_root=True,
        )
        self.assertIn("pill bot errored", html)
        self.assertNotIn("pill bot changes", html)

    def test_success_check_shows_review_pill(self):
        # Check finished cleanly: it renders nothing, the review verdict shows.
        html = c.render_pr(
            _pr([_reviewer_run("agent", "COMPLETED", "SUCCESS")], "CHANGES_REQUESTED"),
            is_root=True,
        )
        self.assertIn("pill bot changes", html)
        self.assertNotIn("pill bot errored", html)
        self.assertNotIn("pill bot pending", html)

    def test_no_check_shows_review_pill(self):
        html = c.render_pr(
            _pr([_check_run("Build", "COMPLETED", "SUCCESS")], "APPROVED"),
            is_root=True,
        )
        self.assertIn("pill bot approved", html)


def _review(login, state, ts="2020-01-01T00:00:00Z", bot=False):
    author = {"login": login}
    if bot:
        author["__typename"] = "Bot"
    return {"author": author, "state": state, "submittedAt": ts}


def _review_pr(reviews, decision, author="me", requested=()):
    return {
        "author": {"login": author},
        "reviewDecision": decision,
        "reviews": {"nodes": list(reviews)},
        "reviewRequests": {"nodes": [
            {"requestedReviewer": {"login": r}} for r in requested
        ]},
    }


class ReviewStateTest(unittest.TestCase):
    def test_human_approval_survives_bot_changes_requested(self):
        # A bot CHANGES_REQUESTED drags GitHub's aggregate reviewDecision to
        # CHANGES_REQUESTED, but a human approved and no human is blocking, so
        # the PR should still read as approved (regression: PR 13615).
        pr = _review_pr(
            [
                _review("github-actions", "CHANGES_REQUESTED", "2020-01-01T00:00:00Z", bot=True),
                _review("me", "COMMENTED", "2020-01-01T00:01:00Z"),
                _review("reviewer", "APPROVED", "2020-01-02T00:00:00Z"),
            ],
            decision="CHANGES_REQUESTED",
        )
        self.assertEqual(c.review_state(pr), ("approved", "Approved"))

    def test_human_changes_requested_still_wins(self):
        pr = _review_pr(
            [_review("reviewer", "CHANGES_REQUESTED")],
            decision="CHANGES_REQUESTED",
        )
        self.assertEqual(c.review_state(pr), ("changes", "Changes requested"))

    def test_review_required_not_shown_as_approved(self):
        # CODEOWNERS still needs someone: a lone human approval is not enough.
        pr = _review_pr(
            [_review("reviewer", "APPROVED")],
            decision="REVIEW_REQUIRED",
        )
        self.assertEqual(c.review_state(pr), ("none", "No reviews"))

    def test_plain_approval(self):
        pr = _review_pr([_review("reviewer", "APPROVED")], decision="APPROVED")
        self.assertEqual(c.review_state(pr), ("approved", "Approved"))


if __name__ == "__main__":
    unittest.main()
