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


if __name__ == "__main__":
    unittest.main()
