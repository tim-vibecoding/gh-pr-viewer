branches with the `integration-branch` label are deploy PRs, which will go into
the merge queue.

for these branches, we should:
- remove the reviews pill
- add a pill that shows the merge queue state
  - if the branch needs to be updated, error state
  - if checks have all passed and the pr is not in the merge queue, error state
  - if the branch is in the queue, pending state
  - if the branch is currently deploying, distinct pending state
  - otherwise, gray state
- update the tooltip of the dot and make it purple

come up with a plan and write it to PLAN.md in this directory
