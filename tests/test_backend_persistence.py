from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path


class BackendPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "kiosk-test.db"
        os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"

        for module_name in ("app", "backend"):
            sys.modules.pop(module_name, None)

        self.app_module = importlib.import_module("app")
        self.client = self.app_module.app.test_client()

    def tearDown(self) -> None:
        for module_name in ("app", "backend"):
            sys.modules.pop(module_name, None)
        os.environ.pop("DATABASE_URL", None)
        self.tmpdir.cleanup()

    def test_weekly_form_save_persists_new_record(self) -> None:
        response = self.client.post(
            "/printers/2/weekly",
            data={
                "technician_name": "Technician 1",
                "semester": "semester_2",
                "week_number": "13",
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
                "note": "Fresh weekly save from test.",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/printers/2/weekly/history?saved=weekly")

        history = self.client.get("/printers/2/weekly/history")
        self.assertEqual(history.status_code, 200)
        self.assertIn(b"week 13", history.data)
        self.assertIn(b"8/8", history.data)
        self.assertIn(b"Fresh weekly save from test.", history.data)

        records = self.app_module.backend.weekly_records(2)
        detail = self.client.get(f"/printers/2/weekly/history/{records[0]['event_id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Filament sensor turned on", detail.data)

        dashboard = self.client.get("/api/dashboard?page=2")
        self.assertEqual(dashboard.status_code, 200)
        printer = next(item for item in dashboard.json["printers"] if item["id"] == 2)
        self.assertEqual(printer["nozzle_life_used_km"], 0.12)
        self.assertEqual(printer["nozzle_life_percent"], 4)
        self.assertEqual(printer["nozzle_life_label"], "0.12 / 3 km")

        dashboard_page = self.client.get("/?page=2")
        self.assertEqual(dashboard_page.status_code, 200)
        self.assertIn(b"Nozzle life", dashboard_page.data)
        self.assertIn(b"0.12 / 3 km", dashboard_page.data)

    def test_dashboard_cards_use_requested_two_by_two_pages(self) -> None:
        expected_pages = {
            1: ["Bell", "Tolstoy", "Einstein", "Socrates"],
            2: ["Picasso", "Beethoven", "Hypatia", "Watt"],
            3: ["H2D", "Rosalind"],
        }

        seen_names = []
        for page, names in expected_pages.items():
            dashboard = self.client.get(f"/api/dashboard?page={page}")
            self.assertEqual(dashboard.status_code, 200)
            self.assertEqual(dashboard.json["total_printers"], 10)
            self.assertEqual(dashboard.json["total_pages"], 3)
            self.assertEqual([printer["name"] for printer in dashboard.json["printers"]], names)
            self.assertLessEqual(len(dashboard.json["printers"]), 4)
            self.assertIn("action_needed", dashboard.json["printers"][0])
            self.assertIn("recent_fault", dashboard.json["printers"][0])
            seen_names.extend(names)

        self.assertEqual(len(seen_names), len(set(seen_names)))

    def test_manual_reactive_save_persists_history_and_dashboard_state(self) -> None:
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
        self.assertEqual(response.headers["Location"], "/printers/2/history?filter=reactive&saved=manual")

        history = self.client.get("/printers/2/history?filter=reactive")
        self.assertEqual(history.status_code, 200)
        self.assertIn(b"Fan checked", history.data)

        dashboard = self.client.get("/api/dashboard?page=2")
        self.assertEqual(dashboard.status_code, 200)
        printer = next(item for item in dashboard.json["printers"] if item["id"] == 2)
        self.assertEqual(printer["status"], "Available")
        self.assertEqual(printer["reactive_state"], "Resolved")
        self.assertEqual(printer["last_fix_summary"], "Fan checked")
        self.assertFalse(printer["has_open_fault"])


if __name__ == "__main__":
    unittest.main()
