# Print Farm Maintenance Kiosk Frontend

Compact Flask kiosk app for an 800x480 Raspberry Pi touchscreen, with a SQLAlchemy backend that can run on PostgreSQL.

## Run

```bash
python3 app.py
```

Then open:

```text
http://127.0.0.1:5050
```

## Included V1 Pages

- Dashboard with 10 active printers on one compact page, including nozzle-life progress
- Printer action/detail page with quick fixes
- Weekly maintenance form
- Weekly history list and read-only detail
- Reactive start page
- Diagnosis assistant from `data/diagnosis_tree.json`
- Manual reactive log
- Reactive summary/save form
- Full history
- Settings page for technician emails and Google Chat webhook
- In-app Markdown SOP viewer

The starter data is seeded into the configured database on first run.

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

## Raspberry Pi 4 Notifications

The Raspberry Pi 4 is the production machine. The MacBook is only for development and testing.

Configure the Pi timezone first:

```bash
sudo timedatectl set-timezone Europe/Paris
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

In the kiosk Settings page, add technician names/emails, check "Receives weekly reminders" for the technicians who should get Monday reminders, and paste the Google Chat incoming webhook URL.

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
