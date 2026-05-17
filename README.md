# Print Farm Maintenance Kiosk Frontend

Compact Flask kiosk app for an 800x480 Raspberry Pi touchscreen, with a SQLAlchemy backend that can run on PostgreSQL.

For the full Raspberry Pi 4 deployment path, use `docs/RASPBERRY_PI_SETUP.md`.
For routine setup checks, use `docs/ADMIN_CHECKLIST.md`.

## Run

```bash
python3 app.py
```

Then open:

```text
http://127.0.0.1:5050
```

## Included V1 Pages

- Dashboard with 10 active printers across 2x2 pages, including nozzle-life progress
- Printer action/detail page with quick fixes
- Weekly maintenance form
- Weekly history list and read-only detail
- Reactive start page
- Diagnosis assistant from `data/diagnosis_tree.json`
- Manual reactive log
- Reactive summary/save form
- Full history
- Settings page for technician emails and Google Chat webhook
- In-app HTML SOP viewer

The starter data is seeded into the configured database on first run.

## SOP Files

Use an HTML folder for picture-based SOPs:

```text
sops/
  nozzle-change/
    instruction.html
    images/
      step-1.png
```

Open the SOP with either `/sops/nozzle-change` or `/sops/instruction`. Image paths such as `images/step-1.png` are served by the kiosk automatically.

## PostgreSQL Backend

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Create a local PostgreSQL database and user:

```bash
createuser maintenance_kiosk
createdb maintenance_kiosk -O maintenance_kiosk
psql -d maintenance_kiosk -c "ALTER USER maintenance_kiosk WITH PASSWORD 'maintenance_kiosk';"
```

Create `.env` from the example:

```bash
cp .env.example .env
```

Then run the app:

```bash
python3 app.py
```

The app reads `DATABASE_URL`, creates the tables if needed, and seeds the initial printer/demo records only when the `printers` table is empty.

If `DATABASE_URL` is not set, the app falls back to `data/kiosk.db` so the UI can still preview without Postgres. For the Raspberry Pi deployment, use PostgreSQL.

The backend code lives in `backend.py`. Flask routes call that repository layer instead of reading in-memory mock data, so weekly maintenance and reactive logs persist.

## Google Sheets Copy

PostgreSQL is still the source of truth. Google Sheets is an operational copy for reports and backup.

University SSO accounts can block service-account access, so this app uses a Google Apps Script webhook instead of logging the Raspberry Pi into Google. Create a Google Sheet, open Apps Script, paste the code from `docs/google_sheets_apps_script_webhook.js`, and deploy it as a web app. In Apps Script project settings, add a script property:

```text
WEBHOOK_SECRET=the-same-long-random-secret-used-on-the-pi
```

Configure the Pi `.env`:

```dotenv
GOOGLE_SHEETS_SYNC_ENABLED=true
GOOGLE_SHEETS_WEBHOOK_URL=https://script.google.com/macros/s/your-deployment-id/exec
GOOGLE_SHEETS_WEBHOOK_SECRET=the-same-long-random-secret-used-in-apps-script
```

The kiosk saves to Postgres first, then tries to send the update to Google Sheets. If Google is offline or blocked, the maintenance save still succeeds and the sync row remains in `sync_queue` for retry.

Backfill existing Postgres data and retry pending sync rows:

```bash
python3 google_sheets_sync_worker.py --backfill
```

Run retries every 5 minutes with cron:

```cron
*/5 * * * * cd /home/pi/maintenance_kiosk && /usr/bin/python3 google_sheets_sync_worker.py >> /home/pi/maintenance_kiosk/google_sheets_sync.log 2>&1
```

## Raspberry Pi 4 Notifications

The Raspberry Pi 4 is the production machine. The MacBook is only for development and testing.

Configure the Pi timezone first:

```bash
sudo timedatectl set-timezone Europe/London
timedatectl
```

Configure Gmail SMTP in `.env`. Use a Gmail app password, not your normal Gmail password:

```dotenv
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-gmail-address@example.com
SMTP_PASSWORD=your-gmail-app-password
SMTP_FROM_EMAIL=your-gmail-address@example.com
```

In the kiosk Settings page, add technician names/emails, check "Receives weekly reminders" for the technicians who should get Monday reminders, and paste the Google Chat URL.

Google Chat supports two modes:

- Direct incoming webhook: paste the Google Chat incoming webhook URL into Settings and leave `GOOGLE_CHAT_WEBHOOK_SECRET` blank.
- Apps Script relay: paste the Apps Script web app URL into Settings and set `GOOGLE_CHAT_WEBHOOK_SECRET` in `.env`. The relay code is in `docs/google_chat_apps_script_relay.js`.

The Apps Script relay still needs a real Google Chat incoming webhook stored in Apps Script. If the university blocks incoming Chat webhooks completely, use admin approval or a managed Google Chat app instead of storing SSO credentials on the Pi.

Run pending notification sends every 5 minutes with cron:

```cron
*/5 * * * * cd /home/pi/maintenance_kiosk && /usr/bin/python3 notification_worker.py >> /home/pi/maintenance_kiosk/notification_worker.log 2>&1
```

Queue weekly reminder emails every Monday at 12:00 Pi local time:

```cron
0 12 * * 1 cd /home/pi/maintenance_kiosk && /usr/bin/python3 weekly_reminder_worker.py >> /home/pi/maintenance_kiosk/weekly_reminder_worker.log 2>&1
```

On the MacBook, these worker scripts can be run manually for testing:

```bash
python3 weekly_reminder_worker.py
python3 notification_worker.py
```

For production on the Pi, prefer the included `systemd` templates in `deploy/systemd/`. They run the Flask app with Gunicorn, retry notifications every 5 minutes, retry Google Sheets sync every 5 minutes, and queue weekly reminders every Monday at 12:00 Pi local time.
