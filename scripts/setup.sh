#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -c 'import sys; assert sys.version_info >= (3, 11), "Python 3.11 or newer is required"'
if [[ ! -x .venv/bin/python ]]; then
    python3 -m venv .venv
fi
if command -v uv >/dev/null 2>&1; then
    export UV_CACHE_DIR="${UV_CACHE_DIR:-${PWD}/.venv/.uv-cache}"
    uv pip sync --python .venv/bin/python --require-hashes requirements.lock
    uv pip check --python .venv/bin/python
else
    .venv/bin/python -m pip install --require-hashes -r requirements.lock
    .venv/bin/python -m pip check
fi
.venv/bin/python -m pytest -q
if command -v node >/dev/null 2>&1; then
    node --check kiosk/static/app.js
fi
