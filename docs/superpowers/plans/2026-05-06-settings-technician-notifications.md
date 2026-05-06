# Settings Technician Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Raspberry Pi 4-ready settings, technician contact storage, weekly reminder email queueing, and Google Chat notification queueing/sending.

**Architecture:** Keep Flask as the kiosk web framework and SQLAlchemy as the database layer. Store technician contacts, app settings, and queued notification work in PostgreSQL or the existing SQLite fallback. Use small Python worker scripts that Raspberry Pi OS can run from cron or systemd timers.

**Tech Stack:** Python 3, Flask, SQLAlchemy Core, PostgreSQL via `psycopg`, SQLite fallback, `smtplib`, `urllib.request`, `unittest`.

---

## File Structure

- `backend.py`: owns SQLAlchemy tables, repository functions, notification queue creation, and small schema upgrades for existing local DB files.
- `app.py`: owns Flask routes, including `/settings`, and passes database technicians into weekly/reactive forms.
- `templates/settings.html`: new kiosk settings screen for technician contacts and Google Chat webhook.
- `templates/dashboard.html`: add Settings button.
- `static/css/kiosk.css`: style settings form using existing kiosk visual language.
- `notifications.py`: pure worker helpers for Gmail SMTP and Google Chat webhook sending.
- `notification_worker.py`: Raspberry Pi cron/systemd entrypoint for sending pending notification jobs.
- `weekly_reminder_worker.py`: Raspberry Pi cron/systemd entrypoint for queueing Monday 12:00 weekly reminders.
- `.env.example`: add Gmail SMTP and kiosk URL variables.
- `README.md`: document Raspberry Pi 4 deployment, timezone, cron/systemd examples, and MacBook test notes.
- `tests/test_settings_notifications.py`: new tests for settings, reminder queueing, Google Chat queueing, and worker send behavior.
- `tests/test_backend_persistence.py`: keep existing persistence tests green.

## Task 0: Stabilize Existing Backend Baseline

**Files:**
- Modify: none
- Test: `tests/test_backend_persistence.py`

- [ ] **Step 1: Run current backend tests**

Run:

```bash
python3 -m unittest discover -s tests -v
```

Expected: existing tests pass.

- [ ] **Step 2: Run Python compile check**

Run:

```bash
python3 -m py_compile app.py backend.py
```

Expected: command exits 0.

- [ ] **Step 3: Commit backend baseline**

Run:

```bash
git add .gitignore README.md app.py backend.py templates/manual_log.html requirements.txt .env.example tests/test_backend_persistence.py
git commit -m "Add SQLAlchemy backend persistence"
```

Expected: commit contains only the existing backend persistence work.

## Task 1: Technician Settings Repository

**Files:**
- Modify: `backend.py`
- Test: `tests/test_settings_notifications.py`

- [ ] **Step 1: Write failing repository tests**

Create `tests/test_settings_notifications.py` with:

```python
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path


class SettingsNotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "kiosk-test.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
        for module_name in ("app", "backend", "notifications"):
            sys.modules.pop(module_name, None)
        self.app_module = importlib.import_module("app")
        self.backend = importlib.import_module("backend")
        self.client = self.app_module.app.test_client()

    def tearDown(self) -> None:
        for module_name in ("app", "backend", "notifications"):
            sys.modules.pop(module_name, None)
        os.environ.pop("DATABASE_URL", None)
        self.tmpdir.cleanup()

    def test_saves_and_lists_active_technicians(self) -> None:
        self.backend.save_technicians(
            [
                {
                    "name": "Alex Technician",
                    "email": "alex@example.com",
                    "receives_weekly_reminders": True,
                    "is_active": True,
                },
                {
                    "name": "No Reminder",
                    "email": "noreminder@example.com",
                    "receives_weekly_reminders": False,
                    "is_active": True,
                },
            ]
        )

        names = [technician["name"] for technician in self.backend.active_technicians()]
        reminder_names = [technician["name"] for technician in self.backend.weekly_reminder_technicians()]

        self.assertEqual(names, ["Alex Technician", "No Reminder"])
        self.assertEqual(reminder_names, ["Alex Technician"])

    def test_google_chat_webhook_setting_round_trips(self) -> None:
        self.backend.save_app_setting("google_chat_webhook_url", "https://chat.googleapis.com/v1/spaces/example")

        self.assertEqual(
            self.backend.get_app_setting("google_chat_webhook_url"),
            "https://chat.googleapis.com/v1/spaces/example",
        )
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python3 -m unittest tests/test_settings_notifications.py -v
```

Expected: FAIL because `backend.save_technicians` is not defined.

- [ ] **Step 3: Add technician and app settings tables**

Modify `backend.py` imports:

```python
from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    func,
    insert,
    select,
    update,
)
```

Add below `reactive_events_table`:

```python
technicians_table = Table(
    "technicians",
    metadata,
    Column("technician_id", Integer, primary_key=True, autoincrement=True),
    Column("name", String(120), nullable=False),
    Column("email", String(255), nullable=False),
    Column("receives_weekly_reminders", Boolean, nullable=False, default=False),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("created_at", String(32), nullable=False),
    Column("updated_at", String(32), nullable=False),
)

app_settings_table = Table(
    "app_settings",
    metadata,
    Column("setting_key", String(120), primary_key=True),
    Column("setting_value", Text, nullable=False),
    Column("updated_at", String(32), nullable=False),
)
```

Add below `seed_database`:

```python
def seed_technicians(conn: Any) -> None:
    created_at = now_text()
    for number in range(1, 9):
        conn.execute(
            insert(technicians_table).values(
                name=f"Technician {number}",
                email=f"technician{number}@example.com",
                receives_weekly_reminders=False,
                is_active=True,
                created_at=created_at,
                updated_at=created_at,
            )
        )
```

Modify `seed_database` after printer seeding:

```python
    seed_technicians(conn)
```

Add repository functions near `find_printer`:

```python
def active_technicians() -> list[dict[str, Any]]:
    stmt = select(technicians_table).where(technicians_table.c.is_active == True).order_by(technicians_table.c.name)
    with engine().connect() as conn:
        rows = conn.execute(stmt).fetchall()
    return [dict(row._mapping) for row in rows]


def weekly_reminder_technicians() -> list[dict[str, Any]]:
    stmt = (
        select(technicians_table)
        .where(technicians_table.c.is_active == True)
        .where(technicians_table.c.receives_weekly_reminders == True)
        .order_by(technicians_table.c.name)
    )
    with engine().connect() as conn:
        rows = conn.execute(stmt).fetchall()
    return [dict(row._mapping) for row in rows]


def save_technicians(technicians: list[dict[str, Any]]) -> None:
    updated_at = now_text()
    with engine().begin() as conn:
        conn.execute(delete(technicians_table))
        for technician in technicians:
            name = technician["name"].strip()
            email = technician["email"].strip()
            if not name or "@" not in email or "." not in email.split("@")[-1]:
                raise ValueError("Technician name and valid email are required.")
            conn.execute(
                insert(technicians_table).values(
                    name=name,
                    email=email,
                    receives_weekly_reminders=bool(technician.get("receives_weekly_reminders")),
                    is_active=bool(technician.get("is_active", True)),
                    created_at=updated_at,
                    updated_at=updated_at,
                )
            )


def get_app_setting(setting_key: str, default: str = "") -> str:
    with engine().connect() as conn:
        row = conn.execute(
            select(app_settings_table.c.setting_value).where(app_settings_table.c.setting_key == setting_key)
        ).fetchone()
    return row.setting_value if row else default


def save_app_setting(setting_key: str, setting_value: str) -> None:
    updated_at = now_text()
    with engine().begin() as conn:
        existing = conn.execute(
            select(app_settings_table.c.setting_key).where(app_settings_table.c.setting_key == setting_key)
        ).fetchone()
        if existing:
            conn.execute(
                update(app_settings_table)
                .where(app_settings_table.c.setting_key == setting_key)
                .values(setting_value=setting_value, updated_at=updated_at)
            )
        else:
            conn.execute(
                insert(app_settings_table).values(
                    setting_key=setting_key,
                    setting_value=setting_value,
                    updated_at=updated_at,
                )
            )
```

- [ ] **Step 4: Run repository tests**

Run:

```bash
python3 -m unittest tests/test_settings_notifications.py -v
```

Expected: PASS for the two new repository tests.

- [ ] **Step 5: Commit repository settings work**

Run:

```bash
git add backend.py tests/test_settings_notifications.py
git commit -m "Add technician settings repository"
```

Expected: commit succeeds.

## Task 2: Settings Page and Dashboard Button

**Files:**
- Modify: `app.py`
- Modify: `templates/dashboard.html`
- Create: `templates/settings.html`
- Modify: `static/css/kiosk.css`
- Test: `tests/test_settings_notifications.py`

- [ ] **Step 1: Add failing settings route tests**

Append to `SettingsNotificationTests`:

```python
    def test_settings_page_loads_from_dashboard_and_saves_form(self) -> None:
        dashboard = self.client.get("/")
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b"/settings", dashboard.data)

        response = self.client.post(
            "/settings",
            data={
                "technician_name": ["Alex Technician", "Morgan Technician"],
                "technician_email": ["alex@example.com", "morgan@example.com"],
                "receives_weekly_reminders": ["0"],
                "google_chat_webhook_url": "https://chat.googleapis.com/v1/spaces/example",
            },
        )

        self.assertEqual(response.status_code, 302)
        technicians = self.backend.active_technicians()
        self.assertEqual([item["name"] for item in technicians], ["Alex Technician", "Morgan Technician"])
        self.assertEqual(
            self.backend.get_app_setting("google_chat_webhook_url"),
            "https://chat.googleapis.com/v1/spaces/example",
        )

    def test_settings_rejects_invalid_email_and_webhook(self) -> None:
        response = self.client.post(
            "/settings",
            data={
                "technician_name": ["Alex Technician"],
                "technician_email": ["not-an-email"],
                "google_chat_webhook_url": "",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"valid email", response.data)

        response = self.client.post(
            "/settings",
            data={
                "technician_name": ["Alex Technician"],
                "technician_email": ["alex@example.com"],
                "google_chat_webhook_url": "http://not-secure.example.com",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn(b"https://", response.data)
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python3 -m unittest tests/test_settings_notifications.py -v
```

Expected: FAIL because dashboard lacks `/settings` and the route is missing.

- [ ] **Step 3: Add helper and settings route**

Modify `app.py` imports:

```python
from flask import Flask, abort, jsonify, redirect, render_template, request, url_for
```

Add below `get_dashboard_payload`:

```python
def technician_names() -> list[str]:
    names = [technician["name"] for technician in backend.active_technicians()]
    return names or TECHNICIANS


def technicians_from_settings_form(form: Any) -> list[dict[str, Any]]:
    names = form.getlist("technician_name")
    emails = form.getlist("technician_email")
    reminder_indexes = set(form.getlist("receives_weekly_reminders"))
    technicians = []
    for index, name in enumerate(names):
        email = emails[index] if index < len(emails) else ""
        if not name.strip() and not email.strip():
            continue
        technicians.append(
            {
                "name": name.strip(),
                "email": email.strip(),
                "receives_weekly_reminders": str(index) in reminder_indexes,
                "is_active": True,
            }
        )
    return technicians
```

Add route below `dashboard_api`:

```python
@app.route("/settings", methods=["GET", "POST"])
def settings():
    if request.method == "POST":
        webhook = request.form.get("google_chat_webhook_url", "").strip()
        if webhook and not webhook.startswith("https://"):
            return "Google Chat webhook must start with https://", 400
        try:
            backend.save_technicians(technicians_from_settings_form(request.form))
        except ValueError as exc:
            return str(exc), 400
        backend.save_app_setting("google_chat_webhook_url", webhook)
        return redirect(url_for("settings", saved="settings"))

    return render_template(
        "settings.html",
        technicians=backend.active_technicians(),
        google_chat_webhook_url=backend.get_app_setting("google_chat_webhook_url"),
        saved=request.args.get("saved"),
    )
```

Modify weekly/manual/reactive render calls to use `technician_names()`:

```python
        technicians=technician_names(),
```

- [ ] **Step 4: Add dashboard button**

Modify `templates/dashboard.html` header metrics:

```html
    <div class="header-metrics">
      <a class="settings-button" href="{{ url_for('settings') }}" aria-label="Settings">Settings</a>
      <span class="sync-pill" data-sync-status>{{ payload.sync_status }}</span>
```

- [ ] **Step 5: Create settings template**

Create `templates/settings.html`:

```html
{% extends "base.html" %}
{% set title = "Settings" %}

{% block content %}
<section class="screen settings-screen">
  <header class="kiosk-header compact">
    <div>
      <h1>Settings</h1>
      <span class="muted">Technicians and notifications</span>
    </div>
    <nav class="header-actions">
      <a href="{{ url_for('dashboard') }}">Dashboard</a>
    </nav>
  </header>

  {% if saved %}
    <p class="success-banner">Settings saved.</p>
  {% endif %}

  <form method="post" class="settings-form">
    <section class="form-panel">
      <h2>Technicians</h2>
      <div class="technician-settings-list" data-technician-settings-list>
        {% for technician in technicians %}
          <div class="technician-settings-row">
            <label>
              <span>Name</span>
              <input name="technician_name" value="{{ technician.name }}" required>
            </label>
            <label>
              <span>Email</span>
              <input name="technician_email" type="email" value="{{ technician.email }}" required>
            </label>
            <label class="checkbox-line">
              <input name="receives_weekly_reminders" type="checkbox" value="{{ loop.index0 }}" {% if technician.receives_weekly_reminders %}checked{% endif %}>
              <span>Receives weekly reminders</span>
            </label>
          </div>
        {% endfor %}
        <div class="technician-settings-row">
          <label>
            <span>Name</span>
            <input name="technician_name" value="">
          </label>
          <label>
            <span>Email</span>
            <input name="technician_email" type="email" value="">
          </label>
          <label class="checkbox-line">
            <input name="receives_weekly_reminders" type="checkbox" value="{{ technicians|length }}">
            <span>Receives weekly reminders</span>
          </label>
        </div>
      </div>
    </section>

    <section class="form-panel">
      <h2>Google Chat</h2>
      <label>
        <span>Incoming webhook URL</span>
        <input name="google_chat_webhook_url" type="url" value="{{ google_chat_webhook_url }}" placeholder="https://chat.googleapis.com/...">
      </label>
    </section>

    <div class="save-row">
      <button class="primary-button" type="submit">Save Settings</button>
      <a class="danger-button" href="{{ url_for('dashboard') }}">Cancel</a>
    </div>
  </form>
</section>
{% endblock %}
```

- [ ] **Step 6: Add settings CSS**

Append to `static/css/kiosk.css`:

```css
.settings-button {
  border: 1px solid var(--line);
  border-radius: 6px;
  color: var(--text);
  padding: 0.45rem 0.65rem;
  text-decoration: none;
}

.settings-form {
  display: grid;
  gap: 1rem;
}

.form-panel {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 1rem;
}

.technician-settings-list {
  display: grid;
  gap: 0.75rem;
}

.technician-settings-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1.2fr) auto;
  gap: 0.75rem;
  align-items: end;
}

.checkbox-line {
  align-items: center;
  display: flex;
  gap: 0.45rem;
  min-height: 44px;
}

.success-banner {
  background: #e8f7ef;
  border: 1px solid #9bd6b6;
  border-radius: 6px;
  color: #145c34;
  padding: 0.7rem 0.9rem;
}
```

- [ ] **Step 7: Run settings page tests**

Run:

```bash
python3 -m unittest tests/test_settings_notifications.py -v
```

Expected: PASS for repository and settings route tests.

- [ ] **Step 8: Commit settings UI**

Run:

```bash
git add app.py templates/dashboard.html templates/settings.html static/css/kiosk.css tests/test_settings_notifications.py
git commit -m "Add technician settings page"
```

Expected: commit succeeds.

## Task 3: Queue Google Chat Notifications for Fault Start and Fix

**Files:**
- Modify: `backend.py`
- Test: `tests/test_settings_notifications.py`

- [ ] **Step 1: Add failing queue tests**

Append to `SettingsNotificationTests`:

```python
    def test_manual_fault_start_queues_google_chat_message(self) -> None:
        self.backend.save_app_setting("google_chat_webhook_url", "https://chat.googleapis.com/v1/spaces/example")

        self.client.post(
            "/printers/2/reactive/start",
            data={
                "next_action": "manual",
                "event_origin": "Technician observed issue",
                "symptom_category": "Fan not spinning",
                "urgency_state": "Unavailable",
                "issue_summary": "Fan stopped during print",
            },
        )

        jobs = self.backend.pending_notification_jobs()
        chat_jobs = [job for job in jobs if job["channel"] == "google_chat"]
        self.assertEqual(len(chat_jobs), 1)
        self.assertIn("Picasso", chat_jobs[0]["body"])
        self.assertIn("Fan stopped during print", chat_jobs[0]["body"])

    def test_fixed_reactive_save_queues_google_chat_message(self) -> None:
        self.backend.save_app_setting("google_chat_webhook_url", "https://chat.googleapis.com/v1/spaces/example")

        self.client.post(
            "/printers/2/reactive/manual",
            data={
                "event_origin": "Technician observed issue",
                "symptom_category": "Fan not spinning",
                "issue_summary": "Fan noisy",
                "action_taken": ["Checked fan"],
                "component_involved": "Fan",
                "result_status": "Fixed and available",
                "technician_name": "Alex Technician",
                "fix_summary": "Fan checked",
            },
        )

        jobs = self.backend.pending_notification_jobs()
        chat_jobs = [job for job in jobs if job["channel"] == "google_chat"]
        self.assertEqual(len(chat_jobs), 1)
        self.assertIn("fixed", chat_jobs[0]["body"].lower())
        self.assertIn("Fan checked", chat_jobs[0]["body"])
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python3 -m unittest tests/test_settings_notifications.py -v
```

Expected: FAIL because `pending_notification_jobs` and chat queue fields are missing.

- [ ] **Step 3: Extend notification queue table**

Modify `notification_queue_table` in `backend.py`:

```python
notification_queue_table = Table(
    "notification_queue",
    metadata,
    Column("notification_id", Integer, primary_key=True, autoincrement=True),
    Column("event_id", String(80), ForeignKey("maintenance_events.event_id", ondelete="CASCADE")),
    Column("printer_id", Integer, ForeignKey("printers.id", ondelete="CASCADE")),
    Column("channel", String(40), nullable=False, default="email"),
    Column("notification_type", String(80), nullable=False, default="general"),
    Column("recipient_email", String(255)),
    Column("target_url", Text),
    Column("subject", Text),
    Column("body", Text),
    Column("status", String(30), nullable=False, default="pending"),
    Column("retry_count", Integer, nullable=False, default=0),
    Column("created_at", String(32), nullable=False),
    Column("last_attempt_at", String(32)),
    Column("sent_at", String(32)),
    Column("error_message", Text),
)
```

Add `text` to the SQLAlchemy imports. Add this dialect-aware schema upgrade helper below `init_database`:

```python
def ensure_notification_queue_columns() -> None:
    required_columns = {
        "channel": "VARCHAR(40) DEFAULT 'email' NOT NULL",
        "notification_type": "VARCHAR(80) DEFAULT 'general' NOT NULL",
        "target_url": "TEXT",
    }
    with engine().begin() as conn:
        if engine().dialect.name == "sqlite":
            existing = {row._mapping["name"] for row in conn.execute(text("PRAGMA table_info(notification_queue)"))}
        else:
            rows = conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'notification_queue'"
                )
            ).fetchall()
            existing = {row._mapping["column_name"] for row in rows}
        for column_name, column_sql in required_columns.items():
            if column_name not in existing:
                conn.execute(text(f"ALTER TABLE notification_queue ADD COLUMN {column_name} {column_sql}"))
```

Call it after `metadata.create_all(engine())`:

```python
    ensure_notification_queue_columns()
```

- [ ] **Step 4: Add notification queue repository functions**

Add below `queue_sync`:

```python
def queue_google_chat_notification(conn: Any, event: str, printer_id: int, notification_type: str, body: str) -> None:
    webhook = get_app_setting("google_chat_webhook_url")
    if not webhook:
        return
    conn.execute(
        insert(notification_queue_table).values(
            event_id=event,
            printer_id=printer_id,
            channel="google_chat",
            notification_type=notification_type,
            target_url=webhook,
            subject=None,
            body=body,
            status="pending",
            retry_count=0,
            created_at=now_text(),
        )
    )


def pending_notification_jobs() -> list[dict[str, Any]]:
    stmt = (
        select(notification_queue_table)
        .where(notification_queue_table.c.status.in_(["pending", "failed"]))
        .order_by(notification_queue_table.c.created_at)
    )
    with engine().connect() as conn:
        rows = conn.execute(stmt).fetchall()
    return [dict(row._mapping) for row in rows]


def printer_name(printer_id: int) -> str:
    printer = find_printer(printer_id)
    return printer["name"] if printer else f"Printer {printer_id}"
```

Modify `ensure_reactive_event` after `queue_sync`:

```python
        queue_google_chat_notification(
            conn,
            new_event_id,
            printer_id,
            "fault_started",
            f"Fault started on {printer_name(printer_id)}: {issue}. Origin: {origin}. Urgency: {urgency}. Time: {created_at}.",
        )
```

Modify `save_reactive_event` after `queue_sync`:

```python
        if event_state == "resolved":
            queue_google_chat_notification(
                conn,
                target_event_id,
                printer_id,
                "fault_fixed",
                f"Fault fixed on {printer_name(printer_id)} by {technician}: {fix_summary or event_summary}. Component: {component or 'Not specified'}. Time: {created_at}.",
            )
```

- [ ] **Step 5: Run Google Chat queue tests**

Run:

```bash
python3 -m unittest tests/test_settings_notifications.py -v
```

Expected: PASS for settings and chat queue tests.

- [ ] **Step 6: Commit Google Chat queueing**

Run:

```bash
git add backend.py tests/test_settings_notifications.py
git commit -m "Queue Google Chat fault notifications"
```

Expected: commit succeeds.

## Task 4: Weekly Reminder Queueing

**Files:**
- Modify: `backend.py`
- Create: `weekly_reminder_worker.py`
- Test: `tests/test_settings_notifications.py`

- [ ] **Step 1: Add failing weekly reminder tests**

Append to `SettingsNotificationTests`:

```python
    def test_weekly_reminder_queues_email_only_for_checked_technicians(self) -> None:
        self.backend.save_technicians(
            [
                {
                    "name": "Reminder Tech",
                    "email": "reminder@example.com",
                    "receives_weekly_reminders": True,
                    "is_active": True,
                },
                {
                    "name": "No Reminder Tech",
                    "email": "noreminder@example.com",
                    "receives_weekly_reminders": False,
                    "is_active": True,
                },
            ]
        )

        queued_count = self.backend.queue_weekly_reminder_emails(kiosk_base_url="http://raspberrypi.local:5050")

        jobs = self.backend.pending_notification_jobs()
        email_jobs = [job for job in jobs if job["channel"] == "email"]
        self.assertEqual(queued_count, 1)
        self.assertEqual(len(email_jobs), 1)
        self.assertEqual(email_jobs[0]["recipient_email"], "reminder@example.com")
        self.assertIn("Weekly maintenance reminder", email_jobs[0]["subject"])
        self.assertIn("http://raspberrypi.local:5050", email_jobs[0]["body"])
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python3 -m unittest tests/test_settings_notifications.py -v
```

Expected: FAIL because `queue_weekly_reminder_emails` is missing.

- [ ] **Step 3: Add weekly reminder queue function**

Add to `backend.py` below `pending_notification_jobs`:

```python
def printers_due_for_weekly() -> list[dict[str, Any]]:
    with engine().connect() as conn:
        rows = conn.execute(select(printers_table).order_by(printers_table.c.display_order)).fetchall()
    return [dict(row._mapping) for row in rows]


def weekly_reminder_body(printers: list[dict[str, Any]], kiosk_base_url: str) -> str:
    lines = [
        "Weekly maintenance reminder",
        "",
        "Please complete weekly maintenance for these printers:",
    ]
    for printer in printers:
        lines.append(f"- {printer['name']} (last weekly: {printer['last_weekly_at'] or 'never'})")
    lines.extend(["", f"Kiosk: {kiosk_base_url}"])
    return "\n".join(lines)


def queue_weekly_reminder_emails(kiosk_base_url: str) -> int:
    recipients = weekly_reminder_technicians()
    due_printers = printers_due_for_weekly()
    if not recipients or not due_printers:
        return 0
    created_at = now_text()
    body = weekly_reminder_body(due_printers, kiosk_base_url)
    with engine().begin() as conn:
        for technician in recipients:
            conn.execute(
                insert(notification_queue_table).values(
                    event_id=None,
                    printer_id=None,
                    channel="email",
                    notification_type="weekly_reminder",
                    recipient_email=technician["email"],
                    target_url=None,
                    subject="Weekly maintenance reminder",
                    body=body,
                    status="pending",
                    retry_count=0,
                    created_at=created_at,
                )
            )
    return len(recipients)
```

- [ ] **Step 4: Add Raspberry Pi worker script**

Create `weekly_reminder_worker.py`:

```python
from __future__ import annotations

import os

import backend


def main() -> None:
    kiosk_base_url = os.environ.get("KIOSK_BASE_URL", "http://127.0.0.1:5050")
    queued = backend.queue_weekly_reminder_emails(kiosk_base_url=kiosk_base_url)
    print(f"Queued {queued} weekly reminder email(s).")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run weekly reminder tests**

Run:

```bash
python3 -m unittest tests/test_settings_notifications.py -v
```

Expected: PASS for reminder queueing tests.

- [ ] **Step 6: Commit weekly reminder queueing**

Run:

```bash
git add backend.py weekly_reminder_worker.py tests/test_settings_notifications.py
git commit -m "Queue weekly maintenance reminders"
```

Expected: commit succeeds.

## Task 5: Notification Sending Worker

**Files:**
- Create: `notifications.py`
- Create: `notification_worker.py`
- Modify: `backend.py`
- Test: `tests/test_settings_notifications.py`

- [ ] **Step 1: Add failing worker tests**

Append to `SettingsNotificationTests`:

```python
    def test_process_pending_notifications_marks_success_sent(self) -> None:
        self.backend.save_technicians(
            [
                {
                    "name": "Reminder Tech",
                    "email": "reminder@example.com",
                    "receives_weekly_reminders": True,
                    "is_active": True,
                }
            ]
        )
        self.backend.queue_weekly_reminder_emails(kiosk_base_url="http://raspberrypi.local:5050")
        notifications = importlib.import_module("notifications")
        sent = []

        def fake_email(job):
            sent.append(("email", job["recipient_email"]))

        def fake_chat(job):
            sent.append(("chat", job["target_url"]))

        result = notifications.process_pending_notifications(send_email=fake_email, send_google_chat=fake_chat)

        self.assertEqual(result["sent"], 1)
        self.assertEqual(sent, [("email", "reminder@example.com")])
        self.assertEqual(self.backend.pending_notification_jobs(), [])

    def test_process_pending_notifications_records_failure(self) -> None:
        self.backend.save_technicians(
            [
                {
                    "name": "Reminder Tech",
                    "email": "reminder@example.com",
                    "receives_weekly_reminders": True,
                    "is_active": True,
                }
            ]
        )
        self.backend.queue_weekly_reminder_emails(kiosk_base_url="http://raspberrypi.local:5050")
        notifications = importlib.import_module("notifications")

        def failing_email(job):
            raise RuntimeError("SMTP unavailable")

        result = notifications.process_pending_notifications(send_email=failing_email)
        jobs = self.backend.pending_notification_jobs()

        self.assertEqual(result["failed"], 1)
        self.assertEqual(jobs[0]["status"], "failed")
        self.assertEqual(jobs[0]["retry_count"], 1)
        self.assertIn("SMTP unavailable", jobs[0]["error_message"])
```

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python3 -m unittest tests/test_settings_notifications.py -v
```

Expected: FAIL because `notifications.py` is missing.

- [ ] **Step 3: Add queue status update functions**

Add to `backend.py`:

```python
def mark_notification_sent(notification_id: int) -> None:
    sent_at = now_text()
    with engine().begin() as conn:
        conn.execute(
            update(notification_queue_table)
            .where(notification_queue_table.c.notification_id == notification_id)
            .values(status="sent", sent_at=sent_at, last_attempt_at=sent_at, error_message=None)
        )


def mark_notification_failed(notification_id: int, error_message: str) -> None:
    failed_at = now_text()
    with engine().begin() as conn:
        row = conn.execute(
            select(notification_queue_table.c.retry_count).where(
                notification_queue_table.c.notification_id == notification_id
            )
        ).fetchone()
        retry_count = int(row.retry_count if row else 0) + 1
        conn.execute(
            update(notification_queue_table)
            .where(notification_queue_table.c.notification_id == notification_id)
            .values(
                status="failed",
                retry_count=retry_count,
                last_attempt_at=failed_at,
                error_message=error_message,
            )
        )
```

- [ ] **Step 4: Create sender module**

Create `notifications.py`:

```python
from __future__ import annotations

import json
import os
import smtplib
import urllib.request
from email.message import EmailMessage
from typing import Any, Callable

import backend


def send_email_job(job: dict[str, Any]) -> None:
    message = EmailMessage()
    message["Subject"] = job["subject"] or "Maintenance kiosk notification"
    message["From"] = os.environ["SMTP_FROM_EMAIL"]
    message["To"] = job["recipient_email"]
    message.set_content(job["body"] or "")

    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ["SMTP_USERNAME"]
    password = os.environ["SMTP_PASSWORD"]

    with smtplib.SMTP(host, port, timeout=20) as smtp:
        smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(message)


def send_google_chat_job(job: dict[str, Any]) -> None:
    payload = json.dumps({"text": job["body"] or ""}).encode("utf-8")
    request = urllib.request.Request(
        job["target_url"],
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        response.read()


def process_pending_notifications(
    send_email: Callable[[dict[str, Any]], None] = send_email_job,
    send_google_chat: Callable[[dict[str, Any]], None] = send_google_chat_job,
) -> dict[str, int]:
    result = {"sent": 0, "failed": 0}
    for job in backend.pending_notification_jobs():
        try:
            if job["channel"] == "email":
                send_email(job)
            elif job["channel"] == "google_chat":
                send_google_chat(job)
            else:
                raise ValueError(f"Unknown notification channel: {job['channel']}")
        except Exception as exc:
            backend.mark_notification_failed(job["notification_id"], str(exc))
            result["failed"] += 1
        else:
            backend.mark_notification_sent(job["notification_id"])
            result["sent"] += 1
    return result
```

- [ ] **Step 5: Create worker entrypoint**

Create `notification_worker.py`:

```python
from __future__ import annotations

import notifications


def main() -> None:
    result = notifications.process_pending_notifications()
    print(f"Sent {result['sent']} notification(s), failed {result['failed']} notification(s).")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run worker tests**

Run:

```bash
python3 -m unittest tests/test_settings_notifications.py -v
```

Expected: PASS for worker success and failure tests.

- [ ] **Step 7: Commit worker**

Run:

```bash
git add backend.py notifications.py notification_worker.py tests/test_settings_notifications.py
git commit -m "Add notification delivery worker"
```

Expected: commit succeeds.

## Task 6: Raspberry Pi Configuration Docs

**Files:**
- Modify: `.env.example`
- Modify: `README.md`
- Test: command checks

- [ ] **Step 1: Update `.env.example`**

Patch `.env.example` to contain:

```dotenv
DATABASE_URL=postgresql+psycopg://maintenance_kiosk:maintenance_kiosk@localhost:5432/maintenance_kiosk
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-gmail-address@example.com
SMTP_PASSWORD=your-gmail-app-password
SMTP_FROM_EMAIL=your-gmail-address@example.com
KIOSK_BASE_URL=http://127.0.0.1:5050
```

- [ ] **Step 2: Update README with Raspberry Pi schedule**

Add to `README.md`:

```markdown
## Raspberry Pi 4 Notifications

The Raspberry Pi 4 is the production machine. Configure the Pi timezone first:

```bash
sudo timedatectl set-timezone Europe/Paris
timedatectl
```

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
```

- [ ] **Step 3: Run compile and test checks**

Run:

```bash
python3 -m py_compile app.py backend.py notifications.py notification_worker.py weekly_reminder_worker.py
python3 -m unittest discover -s tests -v
```

Expected: compile exits 0 and all tests pass.

- [ ] **Step 4: Commit docs**

Run:

```bash
git add .env.example README.md
git commit -m "Document Raspberry Pi notification setup"
```

Expected: commit succeeds.

## Task 7: Final Browser and Route Verification

**Files:**
- Modify: none
- Test: Flask route checks

- [ ] **Step 1: Start local Flask server**

Run:

```bash
python3 -m flask --app app run --host 127.0.0.1 --port 5050 --no-debugger --no-reload
```

Expected: server prints `Running on http://127.0.0.1:5050`.

- [ ] **Step 2: Check core routes**

Run in a second terminal:

```bash
curl -I http://127.0.0.1:5050/
curl -I http://127.0.0.1:5050/settings
curl -I http://127.0.0.1:5050/printers/2/weekly
curl -I http://127.0.0.1:5050/printers/2/reactive/start
```

Expected: each command returns `HTTP/1.1 200 OK`.

- [ ] **Step 3: Run final verification**

Run:

```bash
python3 -m py_compile app.py backend.py notifications.py notification_worker.py weekly_reminder_worker.py
python3 -m unittest discover -s tests -v
```

Expected: compile exits 0 and all tests pass.

- [ ] **Step 4: Commit any final fixes**

Run only if Task 7 required edits:

```bash
git add app.py backend.py templates static tests README.md .env.example notifications.py notification_worker.py weekly_reminder_worker.py
git commit -m "Finalize settings notifications feature"
```

Expected: no commit is needed if Task 7 found no fixes.
