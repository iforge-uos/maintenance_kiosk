# Raspberry Pi 4 Setup Guide

This guide turns the maintenance kiosk into a Raspberry Pi 4 service. The MacBook is still useful for development, but the Pi should be treated as the production machine.

## What Runs On The Pi

- **Flask web app**: serves the dashboard, printer pages, Settings page, SOP pages, and maintenance forms.
- **Local SQLite database**: stores the real maintenance data in `data/kiosk.db`. This is the source of truth for the first Pi test.
- **Google Sheets sync worker**: copies selected local data to Google Sheets through Apps Script.
- **Notification worker**: retries pending email and Google Chat messages.
- **Weekly reminder worker**: queues reminder emails every Monday at 12:00 Pi local time.
- **systemd**: starts the web app on boot and runs the background workers on timers.

## 1. Prepare Raspberry Pi OS

Use Raspberry Pi OS 64-bit if possible.

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip chromium-browser unclutter
```

Set the Pi timezone before configuring weekly reminders. The timer uses the Pi local timezone.

```bash
sudo timedatectl set-timezone Europe/London
timedatectl
```

If your kiosk should use a different local timezone, replace `Europe/London` before enabling the timers.

## 2. Clone The Correct Branch

Do not deploy from `main`. Use Franklin's vibe-coding branch.

```bash
cd /home/pi
git clone -b "Franklin's-vibe-coding" <your-repository-url> maintenance_kiosk
cd /home/pi/maintenance_kiosk
```

## 3. Install Python Dependencies

Use a virtual environment so the kiosk dependencies do not mix with the Pi system Python.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`gunicorn` is included in `requirements.txt` for the production web service. It runs the Flask app more reliably than the Flask development server.

## 4. Use Local SQLite Storage

For first testing, skip Postgres. The app stores data locally in:

```text
/home/pi/maintenance_kiosk/data/kiosk.db
```

This file is a SQLite database. SQLite is a small local database stored as one file, so it does not need a database server, database user, or password.

Back it up by copying the file after the app has been stopped:

```bash
cp /home/pi/maintenance_kiosk/data/kiosk.db /home/pi/kiosk-backup-$(date +%Y%m%d).db
```

## 5. Create The Pi `.env`

```bash
cp .env.example .env
nano .env
```

Recommended Pi values:

```dotenv
DATABASE_URL=
KIOSK_BASE_URL=http://raspberrypi.local:5050

SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-gmail-address@example.com
SMTP_PASSWORD=your-gmail-app-password
SMTP_FROM_EMAIL=your-gmail-address@example.com

GOOGLE_CHAT_WEBHOOK_SECRET=

GOOGLE_SHEETS_SYNC_ENABLED=true
GOOGLE_SHEETS_WEBHOOK_URL=https://script.google.com/macros/s/your-sheets-deployment-id/exec
GOOGLE_SHEETS_WEBHOOK_SECRET=use-a-long-random-secret
```

Use a Gmail app password for SMTP. Do not put your normal Google password in `.env`.

Leave `DATABASE_URL` blank unless you deliberately move to Postgres later.

## 6. Configure Google Sheets Copy

The local SQLite database stays the real database. Google Sheets is only a reporting and backup copy.

1. Create one Google Spreadsheet.
2. Open **Extensions > Apps Script**.
3. Paste `docs/google_sheets_apps_script_webhook.js`.
4. In Apps Script project settings, add:

```text
WEBHOOK_SECRET=the-same-secret-used-in-GOOGLE_SHEETS_WEBHOOK_SECRET
```

5. Deploy as a web app.
6. Paste the deployment URL into `GOOGLE_SHEETS_WEBHOOK_URL`.

Run one backfill after setup:

```bash
cd /home/pi/maintenance_kiosk
.venv/bin/python google_sheets_sync_worker.py --backfill
```

## 7. Configure Google Chat

There are two supported modes.

### Option A: Direct Google Chat Webhook

Create a Google Chat incoming webhook once in the browser after completing university SSO. Paste that URL into the kiosk Settings page.

Leave this blank in `.env`:

```dotenv
GOOGLE_CHAT_WEBHOOK_SECRET=
```

The Pi will post directly to Google Chat with:

```json
{"text": "..."}
```

### Option B: Apps Script Relay

Use this if you want the Pi to send to Apps Script instead of directly to the Chat webhook.

1. Create the Google Chat incoming webhook once in the browser.
2. Create a new Apps Script project.
3. Paste `docs/google_chat_apps_script_relay.js`.
4. Add these Apps Script properties:

```text
WEBHOOK_SECRET=the-same-secret-used-in-GOOGLE_CHAT_WEBHOOK_SECRET
GOOGLE_CHAT_WEBHOOK_URL=the-real-Google-Chat-incoming-webhook-url
```

5. Deploy the Apps Script as a web app.
6. Paste the Apps Script web app URL into the kiosk Settings page.
7. Set this in the Pi `.env`:

```dotenv
GOOGLE_CHAT_WEBHOOK_SECRET=use-a-different-long-random-secret
```

The Pi will post to Apps Script with:

```json
{"secret": "...", "text": "..."}
```

If the university blocks incoming Google Chat webhooks completely, this relay cannot bypass that policy. The correct next step is admin approval or a managed Google Chat app. The Pi should not store SSO cookies, university passwords, or Google OAuth tokens.

## 8. Initialize And Test On The Pi

Initialize tables and seed starter printer data:

```bash
cd /home/pi/maintenance_kiosk
.venv/bin/python -c "from app import initialize_database; initialize_database(); print('database ready')"
```

Run automated tests:

```bash
.venv/bin/python -m unittest discover -s tests
```

Start the web app manually:

```bash
.venv/bin/gunicorn --bind 0.0.0.0:5050 app:app
```

Open:

```text
http://127.0.0.1:5050
```

Manual workflow test:

1. Add technician names and emails in Settings.
2. Check weekly reminders only for technicians who should receive emails.
3. Submit one weekly maintenance record.
4. Trigger one manual fault.
5. Mark that fault fixed.
6. Run `.venv/bin/python notification_worker.py`.
7. Run `.venv/bin/python google_sheets_sync_worker.py`.
8. Confirm kiosk pages, Google Sheets, email, and Google Chat all update.

## 9. Install systemd Services

The repo includes templates in `deploy/systemd/`. They assume:

- repo path: `/home/pi/maintenance_kiosk`
- user: `pi`
- group: `pi`
- virtual environment: `/home/pi/maintenance_kiosk/.venv`

If your Pi username is different, edit the `User=`, `Group=`, `WorkingDirectory=`, `EnvironmentFile=`, and `ExecStart=` paths before copying.

```bash
sudo cp deploy/systemd/maintenance-kiosk*.service /etc/systemd/system/
sudo cp deploy/systemd/maintenance-kiosk*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now maintenance-kiosk.service
sudo systemctl enable --now maintenance-kiosk-notifications.timer
sudo systemctl enable --now maintenance-kiosk-sheets-sync.timer
sudo systemctl enable --now maintenance-kiosk-weekly-reminder.timer
```

Check status:

```bash
systemctl status maintenance-kiosk.service
systemctl list-timers "maintenance-kiosk*"
```

Read logs:

```bash
journalctl -u maintenance-kiosk.service -n 100 --no-pager
journalctl -u maintenance-kiosk-notifications.service -n 100 --no-pager
journalctl -u maintenance-kiosk-sheets-sync.service -n 100 --no-pager
journalctl -u maintenance-kiosk-weekly-reminder.service -n 100 --no-pager
```

## 10. Start Chromium In Kiosk Mode

Create or edit the desktop autostart file:

```bash
mkdir -p ~/.config/lxsession/LXDE-pi
nano ~/.config/lxsession/LXDE-pi/autostart
```

Use:

```text
@xset s off
@xset -dpms
@xset s noblank
@unclutter -idle 0.5
@chromium-browser --kiosk --app=http://127.0.0.1:5050
```

Reboot and confirm the dashboard opens automatically:

```bash
sudo reboot
```

## Troubleshooting

**The web app does not start**

Check `journalctl -u maintenance-kiosk.service -n 100 --no-pager`. Most failures are wrong `.env` paths, an accidentally filled `DATABASE_URL`, or missing Python dependencies.

**Where is the local data stored?**

The local database is:

```text
/home/pi/maintenance_kiosk/data/kiosk.db
```

If you delete this file, the app will create a fresh database and seed starter data again.

**The local database should be backed up**

Stop the app before copying the database:

```bash
sudo systemctl stop maintenance-kiosk.service
cp /home/pi/maintenance_kiosk/data/kiosk.db /home/pi/kiosk-backup-$(date +%Y%m%d).db
sudo systemctl start maintenance-kiosk.service
```

**Weekly reminder did not send at Monday 12:00**

Check timezone and timer status:

```bash
timedatectl
systemctl list-timers "maintenance-kiosk-weekly-reminder*"
journalctl -u maintenance-kiosk-weekly-reminder.service -n 100 --no-pager
```

**Google Sheets did not update**

Run the worker manually:

```bash
cd /home/pi/maintenance_kiosk
.venv/bin/python google_sheets_sync_worker.py --limit 10
```

If it still fails, check the local database `sync_queue` rows and confirm `GOOGLE_SHEETS_WEBHOOK_URL` and `GOOGLE_SHEETS_WEBHOOK_SECRET`.

**Google Chat did not update**

Run:

```bash
cd /home/pi/maintenance_kiosk
.venv/bin/python notification_worker.py
```

If direct webhook mode fails, verify the Settings page URL starts with `https://chat.googleapis.com/`. If relay mode fails, verify the Settings page URL is the Apps Script web app URL and `GOOGLE_CHAT_WEBHOOK_SECRET` matches the Apps Script `WEBHOOK_SECRET`.
