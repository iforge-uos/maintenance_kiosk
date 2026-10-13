#!/usr/bin/env bash
set -euo pipefail

if [[ $(id -u) -eq 0 ]]; then
    printf 'The kiosk browser must run as an ordinary user.\n' >&2
    exit 1
fi
if command -v chromium >/dev/null 2>&1; then
    kiosk_browser=$(command -v chromium)
elif command -v chromium-browser >/dev/null 2>&1; then
    kiosk_browser=$(command -v chromium-browser)
else
    printf 'Chromium is missing. Run scripts/install-pi.sh.\n' >&2
    exit 1
fi

mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/maintenance-kiosk-browser"
chmod 700 "${XDG_CONFIG_HOME:-$HOME/.config}/maintenance-kiosk-browser"
while true; do
    # The app starts offline, without waiting for Google or internet access.
    until curl --fail --silent --max-time 3 http://127.0.0.1:5000/api/health >/dev/null; do
        sleep 2
    done
    "$kiosk_browser" \
        --user-data-dir="${XDG_CONFIG_HOME:-$HOME/.config}/maintenance-kiosk-browser" \
        --ozone-platform=x11 \
        --kiosk --window-size=800,480 --force-device-scale-factor=1 \
        --no-first-run --no-default-browser-check --noerrdialogs \
        --disable-session-crashed-bubble --disable-extensions --disable-sync \
        --disable-background-networking --renderer-process-limit=2 \
        http://127.0.0.1:5000 || true
    sleep 2
done
