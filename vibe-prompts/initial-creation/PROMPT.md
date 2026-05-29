we will create a script that fetches all github pull requests for a user and displays them in a list.
it should:
- display PR stacks and trees in a way that clearly represents their hierarchy
- include three status displays:
  - one for all checks not mentioned below
  - one for all checks that include the text "E2E Tests"
  - one for the check titled "Require Review or Audit Label"
- show whether each PR has been approved, received a "comment without approval" review, or had changes requested

please come up with a plan including tech stack (preferably shell with the `gh` command, node, or python) and save it to PLAN.md.
