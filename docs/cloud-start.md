# Start this cloud environment

Use the existing `/workspace/maintenance_kiosk` checkout. Each cloud task is already isolated; do not create a Git worktree unless explicitly requested. Read the README and any applicable agent instructions before making changes.

The app uses Flask, durable local JSON files, Google Sheets, SMTP and Discord. PostgreSQL and SQLite are not part of the application. Installed dependencies and files survive publication; web servers and workers do not.

## Installation

The saved installation script runs `bash scripts/setup.sh` from `/workspace/maintenance_kiosk`. It installs hash-verified dependencies from `requirements.lock` into the ignored `.venv`, checks compatibility, runs mocked automated tests, and checks JavaScript syntax when Node is available. It does not create cloud files, send messages, change credentials, or start a database. It leaves existing runtime data intact.

## Startup

```bash
cd /workspace/maintenance_kiosk
source .venv/bin/activate
bash scripts/run-dev.sh
```

This runs the web app on loopback port 5000 and a delivery worker. Stop it with Ctrl+C; its worker is stopped by the script. The Raspberry Pi hardware worker is a separate opt-in process (`python -m kiosk hardware`) and should not be run on a cloud host.

The default persistent runtime directory is `/workspace/maintenance_kiosk/instance`. To choose another location, set `KIOSK_DATA_DIR` to an absolute path before starting all processes. Preserve this directory: it includes offline records, settings, pending jobs, and permission-protected credentials. Do not dump its files or process environment when checking connection status. Inspect only the credential-presence flags returned by `/api/state`.

## Readiness

From a second shell:

```bash
cd /workspace/maintenance_kiosk
curl --fail --silent http://127.0.0.1:5000/api/health
.venv/bin/python - <<'PY'
import json
from urllib.request import urlopen
with urlopen('http://127.0.0.1:5000/api/state', timeout=5) as response:
    state = json.load(response)
assert isinstance(state['printers'], list)
assert isinstance(state['records'], list)
assert 'google' in state['settings']
print('Local application state is readable; printers:', len(state['printers']))
PY
```

Use the existing test suite (`.venv/bin/python -m pytest -q`) when validating changed behavior. It exercises record saving, restart recovery, deduplication, credential exclusion, delivery retries, integration adapters, and hardware simulation without touching live external services. A healthy local app does not prove Google, email, Discord, or physical GPIO access.

External integration setup belongs in the app's Settings pages. Google requires an existing workbook owned by `threedprintingiforge@gmail.com` and authorized OAuth/service-account access. Notifications are disabled by default. Live email/Discord tests send messages only when an operator explicitly invokes their test actions. Real GPIO tests require the Raspberry Pi. Missing SOP PDFs remain content to supply.

The restricted cloud network needs the Google API/authentication and Discord hostnames saved in the environment draft. SMTP connectivity additionally depends on the configured mail host and the deployment's TCP/network support; it is not established by the local tests. Never disable TLS or package verification to bypass a connection failure.

Use local requests for validation; do not create localhost preview links in the onboarding UI. The user reviews and publishes environment changes. Saving configuration does not publish a snapshot or prove startup in a new task.
