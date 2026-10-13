# iForge maintenance kiosk

A Flask touchscreen app for weekly maintenance, reactive faults, guided diagnosis, and printer status. Maintenance is entered through the kiosk; **Google Sheets in `threedprintingiforge@gmail.com`’s Google Drive is the shared reporting store**. Durable local files preserve records and queued deliveries during outages. No PostgreSQL or SQLite is required.

The app includes editable Settings, Back navigation, an integration Testing page, configurable SMTP email and Discord webhooks, and a Raspberry Pi GPIO service with a separate mock mode. External integrations start disabled. This is a supervised local kiosk with no login: keep the web server on loopback.

The interface preserves the original **800×480 Raspberry Pi touchscreen** design, stylesheet, logos, and maintenance screens from the repository's `Franklin-vibe-fable` branch at `da4dc271f55b226cb3ac7f905d49b37a6951f2fe`. Keep this appearance and resolution when adding features. The dashboard uses the original 2×2 printer grid, with four printers per page and Mainspace/Digital/Exotic page labels; the default configuration has ten printers, eight weekly checks (including the filament sensor), and a 13-week semester. Settings and Testing use the same compact controls, with scrolling inside their content panes.

**Installing on a Raspberry Pi 3A+?** Follow the [Raspberry Pi setup guide](docs/RASPBERRY_PI_SETUP.md) for the installer, automatic boot, touchscreen browser, updates, and backups. It supports existing Raspberry Pi OS Bookworm or Trixie installations.

## Run locally

Use Python 3.12 and the existing checkout. Cloud tasks already have an isolated environment; no Git worktree is needed.

On a Mac, extract the ZIP and open Terminal in the `maintenance_kiosk` folder. The examples below start from its parent directory; the cloud checkout is at `/workspace/maintenance_kiosk`.

```sh
cd maintenance_kiosk
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python -m kiosk
```

Open <http://127.0.0.1:5000>. Runtime files default to `instance/`; set `KIOSK_DATA_DIR` to an absolute, persistent directory when deploying. Use the same directory for the web app and both workers.

In a separate terminal, run the delivery worker when you are ready to synchronize real records and send configured alerts:

```sh
cd maintenance_kiosk
.venv/bin/python -m kiosk worker
```

On the Raspberry Pi, after configuring and checking the wiring in Settings, run:

```sh
.venv/bin/python -m kiosk hardware
```

The web app does not start the delivery or GPIO worker automatically. Run one delivery worker and one GPIO worker for the kiosk. Production worker execution can contact enabled integrations; opening the app alone is not permission to send test notifications.

## Configure and verify

1. Open **Settings** and configure printers, technicians, checklist items, metrics, and academic week. Save the configuration.
2. Connect an existing Google Sheets workbook owned by `threedprintingiforge@gmail.com` using OAuth or a service account. See [Google setup](docs/setup.md#google-drive-and-sheets).
3. Configure email and Discord destinations in Settings. Store credential values only in their dedicated secret controls.
4. Open **Settings → Testing**. Run the non-sending checks, then individually run any desired Google write, email, or Discord test. Sending tests contact the selected destination and are clearly labelled.
5. Use mock trigger/offline/recovery checks in cloud development. Physical input and LED checks need a Raspberry Pi; a simulation is not a hardware pass.

Google synchronization uses `Printers`, `Weekly_Maintenance`, `Reactive_Maintenance`, and `Activity_Log` tabs. Give staff viewing access and make record corrections through the kiosk. Do not reorder headers or manually alter managed records. Local persistence and notification delivery do not depend on a successful Sheets request.

## Development checks

```sh
.venv/bin/python -m pytest
```

Automated tests mock external services; they must not send real email, Discord messages, or Google writes. Settings diagnostics explicitly distinguish non-sending checks from remote write/send actions. See [validation and recovery](docs/testing.md).

## Documentation and remaining deployment work

- [Raspberry Pi 3A+ installation and operation](docs/RASPBERRY_PI_SETUP.md)
- [Setup, credentials, workers, and backups](docs/setup.md)
- [Testing and verification boundaries](docs/testing.md)
- [Agreed implementation plan and outstanding work](docs/implementation-plan.md)
- [Product requirements](print_farm_maintenance_kiosk_PRD.md)

Real account access, workbook ownership, actual message receipt, and Raspberry Pi wiring require deployment verification. The original three illustrated HTML SOPs and three Markdown SOPs are included in `sops/`. The diagnosis graph still references 23 separate SOP PDFs that are not supplied; add those documents or configure valid links before relying on those graph links. Scheduled reminders/digests, automatic Drive backup/settings mirroring, and additional configuration controls remain follow-up work where marked in the plan.
