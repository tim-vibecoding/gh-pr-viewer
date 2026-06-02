#!/usr/bin/env bash
#
# Install (or uninstall) a macOS LaunchAgent that runs the PR Viewer server
# at login. The server stays up in the background; open the URL in a browser
# whenever you want the latest state.
#
# Usage:
#   scripts/install-launchagent.sh                 # install with defaults
#   scripts/install-launchagent.sh --user octocat  # serve a specific user
#   scripts/install-launchagent.sh --port 9000     # use a different port
#   scripts/install-launchagent.sh --uninstall     # remove the LaunchAgent
#
set -euo pipefail

LABEL="com.github-pr-viewer.server"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"

# Resolve the repo root from this script's location so the agent works no
# matter where the repo lives.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

USER_ARG="@me"
PORT="8765"
HOST="127.0.0.1"
UNINSTALL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)      USER_ARG="$2"; shift 2 ;;
    --port)      PORT="$2"; shift 2 ;;
    --host)      HOST="$2"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ "$UNINSTALL" == "1" ]]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed LaunchAgent ($PLIST)."
  exit 0
fi

PYTHON_BIN="$(command -v python3 || true)"
GH_BIN="$(command -v gh || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "python3 not found on PATH." >&2; exit 1
fi
if [[ -z "$GH_BIN" ]]; then
  echo "gh (GitHub CLI) not found on PATH." >&2; exit 1
fi

# The server shells out to `gh`, so the agent needs a PATH that includes it.
GH_DIR="$(dirname "$GH_BIN")"
PATH_VALUE="$GH_DIR:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"

mkdir -p "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON_BIN}</string>
    <string>${REPO_DIR}/pr_server.py</string>
    <string>--user</string>
    <string>${USER_ARG}</string>
    <string>--host</string>
    <string>${HOST}</string>
    <string>--port</string>
    <string>${PORT}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${REPO_DIR}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>${PATH_VALUE}</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${REPO_DIR}/pr_server.log</string>
  <key>StandardErrorPath</key>
  <string>${REPO_DIR}/pr_server.log</string>
</dict>
</plist>
PLIST_EOF

# Reload so changes take effect immediately.
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "Installed LaunchAgent: $PLIST"
echo "Serving PRs for '${USER_ARG}' at http://${HOST}:${PORT}/"
echo "Logs: ${REPO_DIR}/pr_server.log"
echo "Uninstall with: scripts/install-launchagent.sh --uninstall"
