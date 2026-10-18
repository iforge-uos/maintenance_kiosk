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
3. Open **Settings → Google storage**. Set the account email, **Workbook ID**, optional folder ID, and **Connection method**. The workbook ID is the text between `/d/` and `/edit` in the Google Sheets URL. You can type the ID or load it from a file as described below. The folder ID is the final ID in a Drive folder URL.
4. Choose an authentication method below. Save and test before enabling production sync.

The initial app connects an existing workbook; entering an email address does not connect the account or create a spreadsheet. Folder/workbook IDs are configured directly rather than selected with a Drive file picker. Verify actual ownership and sharing in Drive. The app’s connection check reports what the connected credentials can access.

### OAuth as the owner

Create a Google OAuth client for a web application, configure the consent screen, and authorize the dedicated account. If the consent screen is in testing mode, add `threedprintingiforge@gmail.com` as an allowed test user; testing-mode refresh tokens may expire and need reconnection.

The app requests Sheets access, read-only Drive metadata access for ownership/folder checks, and account email identity. Register the callback URL used by the app as an authorized redirect URI. The local default is `http://127.0.0.1:5000/auth/google/callback`. Keep the scheme, host, and port the same in the browser, client configuration, and callback. Cloud forwarded URLs can need their own registered HTTPS redirect URI.

To load your downloaded OAuth client file:

1. Open **Settings → Google storage**.
2. Under **Google OAuth client JSON**, select **Browse storage**. Select the JSON file that you downloaded from Google Cloud.
3. Select **Store credential** for that control. This saves the credential on the kiosk.
4. Set **Connection method** to **Google account (OAuth)**. Set the intended account email.
5. Enter the **Workbook ID**, or use its **Browse storage** control to load a text or JSON file. A full Google Sheets URL in the file also works.
6. Select **Save settings**.
7. Select **Connect Google account**. Sign in with the intended account and complete Google's consent steps.
8. Open **Settings → Testing**. Select **Check Google connection** before you enable synchronization.

Loading an OAuth client file does not connect the Google account. The consent step is still necessary. The advanced **Authorized-user OAuth JSON** control can also load an existing authorized-user JSON file.

OAuth refresh credentials are stored server-side. Do not paste tokens into ordinary settings fields, commit client/token files, or copy them into documentation.

### Service account alternative

Create a service account in the enabled Google Cloud project and obtain its JSON key using the organization’s normal credential process. Share the existing spreadsheet with the service account’s email as **Editor**. The human account continues to own the workbook. If configuring a Drive folder check, grant the service account appropriate access to that folder as well.

Select **Service account** as the connection method. Under **Service account JSON**, select **Browse storage**, select the JSON key, then select **Store credential**. Save the settings and check the Google connection. A service account does not become `threedprintingiforge@gmail.com` merely because that email is configured as the expected owner. Creating a workbook under unrelated credentials is not a substitute for the requested ownership.

### Files for credentials and workbook IDs

Use UTF-8 text files of no more than **64 KiB** each. Google credentials must be JSON files in the format supplied by Google. Do not use a spreadsheet export or a Google account password file.

The **Workbook ID** control accepts a `.txt` or `.json` file with one value:

- A workbook ID or a full Google Sheets URL on its own.
- A text line in the form `GOOGLE_SPREADSHEET_ID=VALUE`.
- A JSON object with one matching key: `spreadsheet_id`, `spreadsheetId`, or `url`.

Replace `VALUE` with your workbook ID or URL. The app takes the ID from a supported Google Sheets URL. Select **Save settings** to apply the loaded ID. Loading the ID does not replace other fields, such as technician names or semester information. Your other unsaved settings stay in the form.

Select **Browse storage** to open the system file picker. On the Pi touchscreen, browse the Pi folders or a mounted USB drive, select the file, then select **Open**. Select **Store credential** or **Save settings** to save the loaded value. The file picker reads storage on the device that runs the browser. To select a file on your Mac, first open the Pi app through the [SSH tunnel](RASPBERRY_PI_SETUP.md#3-maintain-or-troubleshoot-it). Then use the file picker in the Mac browser. **Store credential** saves it on the Pi.

A loaded credential stays in browser memory until you select **Store credential**. It is not included in browser drafts or ordinary settings exports. File selection does not enable an integration, contact Google or Discord, or send a test message. Keep the source credential files private.

### First sync

Run the Google connection check, then explicitly run the isolated Sheets write/read test if wanted. The write test uses a dedicated test tab; it must not add maintenance records to production history. Review any cleanup error.

Enable Google synchronization and start the delivery worker, or use the explicit sync action. Managed tabs are initialized by writing operations, not by simply opening Settings. Existing local records can be synchronized when connecting the workbook. Check the job status rather than assuming a saved local record is already in Sheets.

Keep the kiosk as the only record editor. Share the workbook with staff as Viewers, and use Sheets filter views/reporting sheets for analysis. Managed cells use raw values, so free text is not evaluated as formulas.

## Email

In Settings → Email, configure SMTP host, port, TLS mode, username, sender, recipients, event rules, and enabled state. Save the password with the SMTP password secret control. For Gmail SMTP, use an authorized app password where the account policy supports it; Google Sheets OAuth does not automatically authorize Gmail SMTP.

Use the email test in Settings → Testing with a chosen recipient. A successful SMTP response means the server accepted the message, not that it reached an inbox; confirm receipt and check spam/filtering separately. TLS verification remains enabled. Production email is delivered by the worker, with pending/failed status retained locally.

## Discord

Create a webhook in each desired Discord channel using Discord's channel integration controls. The URL contains a credential. Keep it out of ordinary notes, exports, source code, and screenshots.

To load the webhook from a file:

1. Open **Settings → Discord webhooks**.
2. Add a destination. Set its name, notification events, and optional printer filter. Save the settings before you store a webhook for a new destination.
3. Under that destination's **Discord webhook URL**, select **Browse storage**. Select its text or JSON file.
4. Select **Store credential** for that destination.
5. Open **Settings → Testing**. Select the destination and send a test Discord message. Confirm that it arrives in the correct channel.
6. Enable the destination and Discord notifications when you are ready. Save the settings.

Use one file for each destination. A `.txt` file can contain the webhook URL alone or one line in the form `DISCORD_WEBHOOK_URL=VALUE`. Replace `VALUE` with the webhook URL. A `.json` file can contain an object with one matching key: `webhook_url`, `discord_webhook_url`, or `url`. Each file must meet the [file limits above](#files-for-credentials-and-workbook-ids). You can still paste a URL into the credential control.

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
