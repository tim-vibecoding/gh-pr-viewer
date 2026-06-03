#!/usr/bin/env bash
#
# Restart the macOS LaunchAgent that runs the PR Viewer server. Useful after
# pulling new code or changing the agent's arguments. Picks up the latest
# pr_server.py / pr_core.py without touching the installed plist.
#
# Usage:
#   scripts/restart-server.sh
#
set -euo pipefail

LABEL="com.github-pr-viewer.server"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

if [[ ! -f "$PLIST" ]]; then
  echo "LaunchAgent not installed ($PLIST)." >&2
  echo "Install it first with: scripts/install-launchagent.sh" >&2
  exit 1
fi

# kickstart -k stops the service if running, then (re)starts it. The target is
# the per-user GUI domain for the current login session.
launchctl kickstart -k "gui/$(id -u)/${LABEL}"

echo "Restarted LaunchAgent: ${LABEL}"
