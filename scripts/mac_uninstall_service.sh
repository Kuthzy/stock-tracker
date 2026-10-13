#!/usr/bin/env bash
# Mac: stop the background tracker and remove it from login items.
LABEL="com.stocktracker.bot"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "Stock tracker background service removed."
