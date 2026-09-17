#!/usr/bin/env bash
#
# Schedules calhub with launchd, the supported way to run a recurring job on
# macOS. cron still works but is deprecated and does not survive as cleanly
# across upgrades.
#
#   ./scripts/install-launchd.sh [interval_seconds]   # default 900 (15 minutes)
#   ./scripts/install-launchd.sh --uninstall

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
LABEL="com.calhub.sync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/Library/Logs/calhub"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
die()  { printf '\033[31m✗  %s\033[0m\n' "$*" >&2; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed $LABEL"
  exit 0
fi

INTERVAL="${1:-900}"
[ "$INTERVAL" -ge 60 ] 2>/dev/null || die "interval must be an integer of at least 60 seconds"
[ -x .venv/bin/calhub ] || die "calhub is not installed in .venv. Run ./scripts/install-macos.sh first."
[ -f config.yaml ] || die "no config.yaml found. Run ./scripts/install-macos.sh first."

mkdir -p "$LOG_DIR" "$(dirname "$PLIST")"

say "Writing $PLIST"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>

  <key>ProgramArguments</key>
  <array>
    <string>$ROOT/.venv/bin/calhub</string>
    <string>sync</string>
    <string>--quiet</string>
  </array>

  <key>WorkingDirectory</key>
  <string>$ROOT</string>

  <key>StartInterval</key>
  <integer>$INTERVAL</integer>

  <!-- Run once at load so a problem surfaces now rather than in 15 minutes. -->
  <key>RunAtLoad</key>
  <true/>

  <!-- Grace period after SIGTERM before launchd escalates to SIGKILL.
       Overlapping runs are not a concern: launchd never starts a second copy
       of a label while the first is still running. -->
  <key>ExitTimeOut</key>
  <integer>300</integer>

  <key>StandardOutPath</key>
  <string>$LOG_DIR/sync.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/sync.err.log</string>

  <key>ProcessType</key>
  <string>Background</string>
</dict>
</plist>
PLISTEOF

say "Loading the agent"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL"

cat <<EOF

Scheduled: every $INTERVAL seconds, and once now.

  Status:     launchctl print gui/$(id -u)/$LABEL | head -20
  Run now:    launchctl kickstart -k gui/$(id -u)/$LABEL
  Logs:       tail -f $LOG_DIR/sync.log
  Errors:     tail -f $LOG_DIR/sync.err.log
  Uninstall:  ./scripts/install-launchd.sh --uninstall

Note: this only runs while the Mac is awake. Events already mirrored stay
visible on the phone while it sleeps; only new changes wait for the next run.
EOF
