# Maintenance Kiosk Admin Checklist

Use this when setting up or maintaining the kiosk.

## Local Database

- For first Raspberry Pi testing, leave `DATABASE_URL` blank in `.env`.
- Confirm the data file exists after the first app run: `data/kiosk.db`.
- Back up the local database before major changes:

```bash
sudo systemctl stop maintenance-kiosk.service
cp /home/pi/maintenance_kiosk/data/kiosk.db /home/pi/kiosk-backup-$(date +%Y%m%d).db
sudo systemctl start maintenance-kiosk.service
```

## Technician Settings

- Open `/settings`.
- Add technician name and email.
- Check **Receives weekly reminders** only for technicians who should receive Monday reminder emails.
- Save settings.
- Run `python3 weekly_reminder_worker.py` manually on the Mac or `.venv/bin/python weekly_reminder_worker.py` on the Pi to queue a test reminder.
- Run the notification worker to send queued emails.

## Google Chat

- Direct mode: paste the Google Chat incoming webhook URL into Settings and leave `GOOGLE_CHAT_WEBHOOK_SECRET` blank.
- Relay mode: paste the Apps Script relay URL into Settings and set `GOOGLE_CHAT_WEBHOOK_SECRET` in `.env`.
- Trigger one manual fault and run the notification worker.
- Mark the fault fixed and run the notification worker again.
- Confirm both messages arrive in Google Chat.

## Google Sheets

- Confirm `.env` has `GOOGLE_SHEETS_SYNC_ENABLED=true`.
- Confirm `.env` has the Apps Script URL and shared secret.
- Run one backfill:

```bash
python3 google_sheets_sync_worker.py --backfill
```

- Submit one weekly maintenance record.
- Submit or fix one reactive maintenance record.
- Confirm the `Printer Status`, `Weekly Maintenance Log`, and `Reactive Maintenance Log` tabs update.

## HTML SOPs

- Put each picture-based SOP in its own folder under `sops/`.
- Put the exported `.html` file in that folder.
- Keep the exported `images/` folder next to the HTML file.
- Open `/sops/<html-file-name-without-extension>` in the kiosk to confirm it renders.

Example:

```text
sops/
  Nozzle change instruction v20251113/
    Nozzlechangeinstructionv20251113.html
    images/
      image1.png
```

## Raspberry Pi Health Check

- `systemctl status maintenance-kiosk.service`
- `systemctl list-timers "maintenance-kiosk*"`
- `journalctl -u maintenance-kiosk.service -n 100 --no-pager`
- Open `http://127.0.0.1:5050` on the Pi.
- Confirm dashboard pages: `Mainspace`, `Digital`, `Exotic`.
- Confirm SOP pages load pictures.
- Confirm printer cards fit on the kiosk screen.
