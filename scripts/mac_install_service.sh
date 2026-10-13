#!/usr/bin/env bash
# Mac: run the tracker in the background, start it at login, restart it if it crashes,
# and keep the Mac awake while it runs (the lid must stay open unless an external
# display and power are connected). Undo with scripts/mac_uninstall_service.sh
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="$(pwd)"
LABEL="com.stocktracker.bot"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

case "$DIR" in
  "$HOME/Desktop"*|"$HOME/Documents"*|"$HOME/Downloads"*|"$HOME/Library/Mobile Documents"*)
    echo "This folder is in Desktop, Documents, Downloads or iCloud Drive, where macOS blocks"
    echo "background services. Move the folder to your home folder (e.g. ~/stock-tracker) and run this again."
    exit 1 ;;
esac
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  echo "Python 3.11 or newer is needed: https://www.python.org/downloads/"
  exit 1
fi
if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  rm -rf .venv
  python3 -m venv .venv || { rm -rf .venv; echo "Could not create a Python environment."; exit 1; }
fi
.venv/bin/python -m pip install -q -r requirements.txt
[ -f .env ] || cp .env.example .env
mkdir -p data/logs "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string><string>-i</string>
    <string>$DIR/.venv/bin/python</string><string>-m</string><string>tracker</string><string>run</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$DIR/data/logs/mac-service.log</string>
  <key>StandardErrorPath</key><string>$DIR/data/logs/mac-service.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo "Starting... (checking in 15 seconds)"
sleep 15
if curl -fsS http://localhost:8080/health >/dev/null 2>&1; then
  echo "Stock tracker is running in the background and will start at every login."
  echo "Live log:  tail -f \"$DIR/data/logs/mac-service.log\""
  echo "Health:    curl http://localhost:8080/health"
else
  echo "The tracker did not start. Last lines of its log:"
  tail -n 20 "$DIR/data/logs/mac-service.log" 2>/dev/null || echo "(no log yet)"
  exit 1
fi
