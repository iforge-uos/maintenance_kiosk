#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
    printf 'Run bash scripts/setup.sh first.\n' >&2
    exit 1
fi
.venv/bin/python -m kiosk worker &
kiosk_worker_pid=$!
cleanup() {
    kill "$kiosk_worker_pid" 2>/dev/null || true
    wait "$kiosk_worker_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
.venv/bin/python -m kiosk serve "$@"
