we're going to update this script so it can be run via the command line the same way it currently is,
but it can also run as a local server.

we'll start by pulling the bulk of the logic out of `pr_viewer.py` into a file where it can be reused
between a script and a server, then we'll build a server, then we'll update the README to document
the server option.

start by writing a plan in PLAN.md in this directory. in the plan, decide how we'll create the server,
what code will move, and how we'll structure the README.
