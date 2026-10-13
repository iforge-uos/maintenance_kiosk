#!/usr/bin/env bash
set -euo pipefail

# LightDM supplies DISPLAY/XAUTHORITY and starts this as the ordinary kiosk user.
xset s off || true
xset s noblank || true
xset -dpms || true
openbox &
kiosk_window_manager=$!
unclutter -idle 3 -root &
kiosk_cursor=$!
cleanup() {
    kill "$kiosk_window_manager" "$kiosk_cursor" 2>/dev/null || true
}
trap cleanup EXIT
/opt/maintenance-kiosk/current/scripts/kiosk-browser.sh
