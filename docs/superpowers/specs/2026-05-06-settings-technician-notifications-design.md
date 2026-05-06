# Settings, Technician Contacts, and Notifications Design

## Summary

Add a dashboard Settings button so technicians can manage contact details and notification preferences. The first notification version will support weekly maintenance reminder emails every Monday at 12:00 local Raspberry Pi time, plus Google Chat messages when manually triggered faults start and when repairs are marked fixed.

The production target is a Raspberry Pi 4. The MacBook is only the development and test machine. The implementation will use a queue-first design. Flask route handlers will save user actions and enqueue notification work in PostgreSQL through SQLAlchemy. Separate lightweight worker functions or scripts will send pending email and Google Chat jobs, so form saves stay fast and notifications can retry after network failure.

## User Decisions

- Google Chat integration will use an incoming webhook URL.
- Weekly maintenance notifications are reminders, not completion receipts.
- The production hardware is Raspberry Pi 4; MacBook runs are only for testing.
- Weekly reminders should run every Monday at 12:00 using the Raspberry Pi's local system time.
- Reminder email sending will use Gmail SMTP.
- Only technicians marked with a reminder checkbox receive weekly reminder emails.

## Architecture

The existing Flask app remains the web framework. Flask will render the new Settings page, validate form submissions, and call backend repository functions. SQLAlchemy remains the database framework and will define new tables for technician settings, app settings, and queued notification jobs.

The app will not send email or Google Chat messages directly inside the form request. Instead, app actions create rows in a notification queue. A worker command can be run by a Raspberry Pi OS scheduler, such as a systemd timer or cron, to process pending jobs.

This keeps the kiosk local-first. If the internet is down, the database still records the event and the pending notification remains available for retry.

## Hardware Deployment

Raspberry Pi 4 is the real runtime target. Implementation choices must stay light enough for that hardware:

- Use plain Flask, SQLAlchemy, PostgreSQL, and small Python worker scripts.
- Do not introduce a heavy background framework such as Celery for this version.
- Scheduling should work on Raspberry Pi OS through cron or systemd timers.
- Reminder time calculations use the Raspberry Pi's configured local timezone.
- The Raspberry Pi clock/timezone must be configured correctly during deployment; the MacBook can test the same behavior but does not define production time.
- The kiosk should still run in local-first mode if internet access is unavailable.

## Settings UI

Add a Settings button to the dashboard header. The Settings page will include:

- Technician list with name and email fields.
- Checkbox named "Receives weekly reminders".
- Add technician action.
- Edit existing technician contact details.
- Remove or deactivate technician entry.
- Google Chat webhook URL field.
- A short save confirmation state after updates.

The visible Settings page will not show the Gmail password. Gmail SMTP secrets will live in `.env` because that file is ignored by git and is not displayed in the browser.

## Database Changes

Add a `technicians` table:

- `technician_id`
- `name`
- `email`
- `receives_weekly_reminders`
- `is_active`
- `created_at`
- `updated_at`

Add an `app_settings` table:

- `setting_key`
- `setting_value`
- `updated_at`

Store `google_chat_webhook_url` in `app_settings`.

Extend notification queue usage so it can represent both email and Google Chat jobs:

- Email reminder jobs store recipient email, subject, and body.
- Google Chat jobs store the webhook target and message body.
- Jobs have status values such as `pending`, `sent`, and `failed`.
- Retry metadata remains in the queue so failed sends can be attempted on a future worker run.

## Notification Behavior

Weekly reminder:

- A reminder worker runs every Monday at 12:00 using the Raspberry Pi's local system time.
- It finds active technicians with `receives_weekly_reminders` checked.
- It finds printers whose weekly maintenance should be done for the current week.
- It queues one email per selected technician.
- The email body lists printers needing weekly maintenance and links users back to the kiosk URL when configured.

Fault started:

- When the manual reactive flow starts a fault, the backend queues a Google Chat message.
- The message includes printer name, fault summary, event origin if available, urgency/state, and timestamp.

Fault fixed:

- When a reactive event is saved as `Fixed and available`, the backend queues a Google Chat message.
- The message includes printer name, technician name, fix summary, component if available, and timestamp.

## Configuration

Add `.env.example` entries for Gmail SMTP:

- `SMTP_HOST=smtp.gmail.com`
- `SMTP_PORT=587`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_FROM_EMAIL`
- `KIOSK_BASE_URL=http://127.0.0.1:5050`

The Google Chat webhook is saved from Settings instead of `.env` so it can be changed from the kiosk UI.

On the MacBook, `KIOSK_BASE_URL=http://127.0.0.1:5050` is fine for testing. On the Raspberry Pi, `KIOSK_BASE_URL` should be changed to the Pi's reachable kiosk URL, such as a local hostname or LAN IP address.

## Error Handling

Settings validation:

- Technician name is required.
- Email must contain `@` and a domain portion.
- Google Chat webhook may be blank, but if present it must start with `https://`.

Notification sending:

- Failed email or Google Chat sends update the queue row to `failed`, increment retry count, and store the error message.
- The worker processes only pending or retryable failed jobs.
- The kiosk does not block form saves when sending fails.

## Testing

Add tests for:

- Settings page loads from the dashboard button.
- Saving technicians persists name, email, and reminder checkbox.
- Technician dropdowns use saved active technicians instead of only hardcoded defaults.
- Weekly reminder job creation queues email only for checked technicians.
- Weekly reminder scheduling uses the Raspberry Pi/local system timezone contract, not a hardcoded MacBook assumption.
- Manual fault start queues a Google Chat notification when a webhook exists.
- Fixed reactive save queues a Google Chat notification when a webhook exists.
- Notification sender marks successful jobs as sent.
- Notification sender records failed delivery attempts without deleting the job.

No real Gmail or Google Chat network calls should be used in tests. Tests will use fake sender functions so we can verify queue behavior safely.
