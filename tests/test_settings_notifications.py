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

    def test_saved_technicians_appear_in_maintenance_dropdowns(self) -> None:
        self.backend.save_technicians(
            [
                {
                    "name": "Alex Technician",
                    "email": "alex@example.com",
                    "receives_weekly_reminders": True,
                    "is_active": True,
                }
            ]
        )

        weekly = self.client.get("/printers/2/weekly")
        manual = self.client.get("/printers/2/reactive/manual")

        self.assertEqual(weekly.status_code, 200)
        self.assertEqual(manual.status_code, 200)
        self.assertIn(b"Alex Technician", weekly.data)
        self.assertIn(b"Alex Technician", manual.data)

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


if __name__ == "__main__":
    unittest.main()
