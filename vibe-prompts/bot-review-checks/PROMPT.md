the bot reviewer runs via github checks starting with "PR Reviewer". we should
display these checks in the bot review pill, rather than in the main checks
pill. please come up with a plan to update this pill to display a pending
status when any "PR Reviewer" checks are running, and filter them out of the
main checks. keep in mind that we'll need to handle the case where these checks
fail, and the ui should be distinct from changes requested. write your plan to
PLAN.md in this directory.
