from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select


class GoogleSheetsSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "kiosk-test.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
        os.environ["GOOGLE_SHEETS_SYNC_ENABLED"] = "true"
        os.environ["GOOGLE_SHEETS_WEBHOOK_URL"] = "https://script.google.com/macros/s/example/exec"
        os.environ["GOOGLE_SHEETS_WEBHOOK_SECRET"] = "test-secret"

        for module_name in ("app", "backend", "google_sheets_webhook", "google_sheets_sync_worker"):
            sys.modules.pop(module_name, None)

        self.app_module = importlib.import_module("app")
        self.backend = importlib.import_module("backend")
        self.webhook = importlib.import_module("google_sheets_webhook")
        self.client = self.app_module.app.test_client()

    def tearDown(self) -> None:
        for module_name in ("app", "backend", "google_sheets_webhook", "google_sheets_sync_worker"):
            sys.modules.pop(module_name, None)
        for key in (
            "DATABASE_URL",
            "GOOGLE_SHEETS_SYNC_ENABLED",
            "GOOGLE_SHEETS_WEBHOOK_URL",
            "GOOGLE_SHEETS_WEBHOOK_SECRET",
        ):
            os.environ.pop(key, None)
        self.tmpdir.cleanup()

    def sync_rows(self) -> list[dict]:
        with self.backend.engine().connect() as conn:
            rows = conn.execute(select(self.backend.sync_queue_table)).fetchall()
        return [dict(row._mapping) for row in rows]

    def test_weekly_save_posts_weekly_log_and_printer_status(self) -> None:
        sent_payloads = []
        self.webhook.send_payload = lambda payload: sent_payloads.append(payload)

        response = self.client.post(
            "/printers/2/weekly",
            data={
                "technician_name": "Technician 1",
                "semester": "semester_2",
                "week_number": "14",
                "nozzle_debris_brushed": "on",
                "wiper_screw_tightened": "on",
                "fan_screw_tightened": "on",
                "enclosure_debris_cleaned": "on",
                "bed_cleaned": "on",
                "glue_reapplied": "on",
                "enclosure_fan_ok": "on",
                "filament_sensor_turned_on": "on",
                "x_movement_km": "1.2",
                "y_movement_km": "1.1",
                "z_movement_m": "2.3",
                "filament_m": "120",
                "total_print_hours": "12",
                "note": "Sync weekly test.",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual([payload["type"] for payload in sent_payloads], ["weekly_log", "printer_status"])
        weekly_row = sent_payloads[0]["rows"][0]
        self.assertEqual(weekly_row["printer_name"], "Picasso")
        self.assertEqual(weekly_row["technician"], "Technician 1")
        self.assertEqual(weekly_row["week_number"], 14)
        self.assertEqual(weekly_row["filament_m"], 120.0)
        status_row = sent_payloads[1]["rows"][0]
        self.assertEqual(status_row["printer_name"], "Picasso")
        self.assertEqual(status_row["nozzle_life_label"], "0.12 / 3 km")
        self.assertTrue(all(row["status"] == "completed" for row in self.sync_rows()))

    def test_webhook_failure_does_not_block_postgres_save(self) -> None:
        def fail_send(_payload):
            raise RuntimeError("Google blocked this request")

        self.webhook.send_payload = fail_send

        response = self.client.post(
            "/printers/2/reactive/manual",
            data={
                "event_origin": "Technician observed issue",
                "symptom_category": "Fan not spinning",
                "issue_summary": "Fan noisy",
                "action_taken": ["Checked fan"],
                "component_involved": "Fan",
                "result_status": "Fixed and available",
                "technician_name": "Technician 2",
                "fix_summary": "Fan checked",
            },
        )

        self.assertEqual(response.status_code, 302)
        printer = self.backend.find_printer(2)
        self.assertEqual(printer["status"], "Available")
        self.assertTrue(all(row["status"] == "failed" for row in self.sync_rows()))
        self.assertTrue(all("Google blocked" in row["error_message"] for row in self.sync_rows()))

    def test_retry_worker_marks_failed_syncs_completed_after_success(self) -> None:
        self.webhook.send_payload = lambda _payload: (_ for _ in ()).throw(RuntimeError("temporary outage"))
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
        self.assertTrue(all(row["status"] == "failed" for row in self.sync_rows()))

        sent_payloads = []
        self.webhook.send_payload = lambda payload: sent_payloads.append(payload)
        worker = importlib.import_module("google_sheets_sync_worker")

        sent_count = worker.run_sync()

        self.assertEqual(sent_count, 2)
        self.assertEqual([payload["type"] for payload in sent_payloads], ["reactive_log", "printer_status"])
        self.assertTrue(all(row["status"] == "completed" for row in self.sync_rows()))

    def test_backfill_queues_existing_history_and_current_status(self) -> None:
        queued_count = self.backend.queue_google_sheets_backfill()
        rows = self.sync_rows()

        self.assertEqual(queued_count, len(rows))
        self.assertEqual(sum(1 for row in rows if row["export_type"] == "printer_status"), 10)
        self.assertTrue(any(row["export_type"] == "weekly_log" for row in rows))
        self.assertTrue(any(row["export_type"] == "reactive_log" for row in rows))


if __name__ == "__main__":
    unittest.main()
