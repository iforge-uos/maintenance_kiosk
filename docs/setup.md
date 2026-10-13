# Setup and operation

For a Raspberry Pi 3A+ with Raspberry Pi OS already installed, use the [Raspberry Pi installer guide](RASPBERRY_PI_SETUP.md). It configures the three services, OS GPIO support, and fullscreen browser automatically. The manual runtime instructions below remain useful for development and other deployments.

## Runtime

Use the existing repository checkout and Python 3.12. Install `requirements.lock` in a virtual environment, then use `python -m kiosk`, `python -m kiosk worker`, and `python -m kiosk hardware` as described in the [README](../README.md). No database service is needed. Existing PostgreSQL installations from earlier onboarding are not used; removing unrelated installations or data is not part of app setup.

The web app binds to `127.0.0.1:5000` by default. Use a local browser or a secure forwarded port in cloud development. The kiosk has no user authentication; its Settings page can change credentials and operate hardware. Do not expose it directly to an untrusted network.

Select a persistent runtime directory with an absolute path:

```sh
export KIOSK_DATA_DIR=/workspace/maintenance-kiosk-data
.venv/bin/python -m kiosk
```

Set the same value in each worker terminal or service. Keep runtime files outside source control. All processes must run as the same dedicated operating-system user so file permissions protect secrets and allow inter-process access.

## Google Drive and Sheets

### Prepare the workbook

1. Sign in to Google Drive as `threedprintingiforge@gmail.com` and create a dedicated maintenance spreadsheet. Place it in the desired Drive folder.
2. In Google Cloud, enable the **Google Sheets API** and **Google Drive API** in the project used by the credentials.
3. In Settings → Google, set the account email, spreadsheet ID, optional folder ID, and authentication mode. The spreadsheet ID is the segment between `/d/` and `/edit` in its URL; the folder ID is the final ID in the folder URL.
4. Choose an authentication method below. Save and test before enabling production sync.

The initial app connects an existing workbook; entering an email address does not connect the account or create a spreadsheet. Folder/workbook IDs are configured directly rather than selected with a Drive file picker. Verify actual ownership and sharing in Drive. The app’s connection check reports what the connected credentials can access.

### OAuth as the owner

Create a Google OAuth client for a web application, configure the consent screen, and authorize the dedicated account. If the consent screen is in testing mode, add `threedprintingiforge@gmail.com` as an allowed test user; testing-mode refresh tokens may expire and need reconnection.

The app requests Sheets access, read-only Drive metadata access for ownership/folder checks, and account email identity. Download the OAuth client JSON and save it in the Settings secret control for the Google OAuth client. Register the callback URL used by the app as an authorized redirect URI; the local default is `http://127.0.0.1:5000/auth/google/callback`. Use the Settings Google authorization action and complete consent with the intended account. Keep the exact scheme/host/port consistent between the browser, the client configuration, and callback. Cloud forwarded URLs may need their own registered HTTPS redirect URI.

OAuth refresh credentials are stored server-side. Do not paste tokens into ordinary settings fields, commit client/token files, or copy them into documentation.

### Service account alternative

Create a service account in the enabled Google Cloud project and obtain its JSON key using the organization’s normal credential process. Share the existing spreadsheet with the service account’s email as **Editor**. The human account continues to own the workbook. If configuring a Drive folder check, grant the service account appropriate access to that folder as well.

Select service-account mode and place the JSON key in its dedicated Settings secret control. A service account does not become `threedprintingiforge@gmail.com` merely because that email is configured as the expected owner. Creating a workbook under unrelated credentials is not a substitute for the requested ownership.

### First sync

Run the Google connection check, then explicitly run the isolated Sheets write/read test if wanted. The write test uses a dedicated test tab; it must not add maintenance records to production history. Review any cleanup error.

Enable Google synchronization and start the delivery worker, or use the explicit sync action. Managed tabs are initialized by writing operations, not by simply opening Settings. Existing local records can be synchronized when connecting the workbook. Check the job status rather than assuming a saved local record is already in Sheets.

Keep the kiosk as the only record editor. Share the workbook with staff as Viewers, and use Sheets filter views/reporting sheets for analysis. Managed cells use raw values, so free text is not evaluated as formulas.

## Email

In Settings → Email, configure SMTP host, port, TLS mode, username, sender, recipients, event rules, and enabled state. Save the password with the SMTP password secret control. For Gmail SMTP, use an authorized app password where the account policy supports it; Google Sheets OAuth does not automatically authorize Gmail SMTP.

Use the email test in Settings → Testing with a chosen recipient. A successful SMTP response means the server accepted the message, not that it reached an inbox; confirm receipt and check spam/filtering separately. TLS verification remains enabled. Production email is delivered by the worker, with pending/failed status retained locally.

## Discord

Create a webhook in each desired Discord channel using Discord’s channel integration controls. In Settings → Discord, add a named destination with a stable ID, enable it, select events and optional printer IDs, and save its URL in the destination’s secret control. The URL contains a credential: do not put it in ordinary notes, exports, source code, or screenshots.

Each destination can receive fault, escalation, resolution, or weekly-completion events as supported by the UI. A blank printer filter means all printers. Use Settings → Testing to send a labelled message to that destination. Verify its arrival in the intended channel. Test messages suppress mentions.

The worker respects retry/rate-limit responses and records returned Discord message IDs. A timeout after the remote service accepted a message can produce a duplicate; incident/change IDs identify the same underlying event. Do not change destinations while jobs remain pending; settle or investigate those jobs before migrating.

## GPIO on the Raspberry Pi

Cloud development uses the `mock` backend. Select `gpiozero` only on a Raspberry Pi with the correct GPIO driver available. The settings use **BCM GPIO numbering**, not physical header positions. Confirm printer-to-button/LED mapping, pin uniqueness, pull-up configuration, LED polarity, hold time, and debounce time.

`gpiozero` is included in the locked Python dependencies. Install compatible Raspberry Pi OS GPIO driver packages for the board and pin factory; mock mode alone does not validate the operating-system GPIO driver. Follow the Pi/LED/button electrical specifications when wiring and use the appropriate LED resistors.

Hardware diagnostics take a short-lived lease that pauses the production watcher and releases its pins. Run the bounded input test in Settings → Testing, press/hold the actual button, and observe the measured result. LED tests restore the prior output state; confirm the actual light visually. The watcher resumes when the diagnostic releases its lease. If another process owns the pins, stop that process before retrying; do not run multiple GPIO workers.

The worker uses the same maintenance service as the UI: qualifying holds create or continue an open incident, while resolving the fault updates availability and LED state. The running GPIO worker reloads mapping/timing changes and refreshes outputs. Switching away from `gpiozero` stops monitoring; start the worker again after switching back to real hardware mode.

## Service deployment

The [Raspberry Pi installer](RASPBERRY_PI_SETUP.md) creates the services and browser startup described here, with persistent data in `/var/lib/maintenance-kiosk`.

On the Pi, configure a process supervisor such as `systemd` to run the three commands as the same dedicated kiosk user, from the repository directory, with the same `KIOSK_DATA_DIR` and the absolute virtual-environment Python path. Set a restart policy and ensure the durable data directory is writable. Launch the touchscreen browser in kiosk mode against the loopback URL after the web service is available.

Do not start the hardware service on cloud hosts. Confirm reboot recovery and LED state on the actual Pi before relying on boot autostart. Supervisor unit installation is a deployment step, not a result of running the development commands.

## Files, backups, and recovery

Local files retain maintenance changes, settings revisions, and delivery outcomes. Successful local saving does not imply that Google or notifications have accepted a change. Stop the app/workers before copying the runtime directory for a consistent disaster-recovery snapshot.

The UI exports non-secret settings and maintenance data. Those downloads deliberately exclude credentials and do not constitute a complete live queue/secret backup. Protect a full runtime backup as sensitive; the credential store is permission-restricted plaintext on disk, not encrypted by an external secret manager. Restore only with all kiosk processes stopped and start one delivery worker afterward to reconcile unfinished deliveries.

Automatic Drive upload of backups/settings, scheduled retention, and restoration from a cloud activity log alone are follow-up work unless explicitly implemented in the running build. A saved backup setting does not establish that a scheduler ran. Preserve the Pi runtime directory and confirm your backup/restore process before production use.
