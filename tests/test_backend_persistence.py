from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path


class BackendConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = ""
        sys.modules.pop("backend", None)

    def tearDown(self) -> None:
        sys.modules.pop("backend", None)
        os.environ.pop("DATABASE_URL", None)
        self.tmpdir.cleanup()

    def test_blank_database_url_uses_local_sqlite_file(self) -> None:
        backend = importlib.import_module("backend")
        backend.DEFAULT_SQLITE_PATH = Path(self.tmpdir.name) / "data" / "kiosk.db"

        self.assertEqual(
            backend.database_url(),
            f"sqlite:///{Path(self.tmpdir.name) / 'data' / 'kiosk.db'}",
        )


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

    def test_sop_viewer_renders_html_folder_and_assets(self) -> None:
        sop_dir = Path(self.tmpdir.name) / "sops"
        sop_folder = sop_dir / "nozzle-change"
        image_folder = sop_folder / "images"
        image_folder.mkdir(parents=True)
        (image_folder / "step.png").write_bytes(b"fake image bytes")
        (sop_folder / "instruction.html").write_text(
            (
                "<html><head><style>.step-photo{max-width:100%;}</style></head>"
                "<body><h1>Nozzle Change</h1>"
                '<img class="step-photo" src="images/step.png" alt="Nozzle step">'
                "</body></html>"
            )
        )
        self.app_module.SOP_DIR = sop_dir

        response = self.client.get("/sops/nozzle-change")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Nozzle Change", response.data)
        self.assertIn(b".step-photo", response.data)
        self.assertIn(b"/sop-assets/nozzle-change/images/step.png", response.data)

        file_slug_response = self.client.get("/sops/instruction")
        self.assertEqual(file_slug_response.status_code, 200)
        self.assertIn(b"Nozzle Change", file_slug_response.data)
        self.assertIn(b"/sop-assets/nozzle-change/images/step.png", file_slug_response.data)

        asset_response = self.client.get("/sop-assets/nozzle-change/images/step.png")
        self.assertEqual(asset_response.status_code, 200)
        self.assertEqual(asset_response.data, b"fake image bytes")
        asset_response.close()

    def test_diagnosis_first_step_has_back_link_to_category_picker(self) -> None:
        response = self.client.get("/printers/2/diagnosis?category=filament_not_sticking")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b">Back<", response.data)
        self.assertIn(b'href="/printers/2/diagnosis"', response.data)
        self.assertIn(b"path=q_filament_not_sticking_1", response.data)

    def test_diagnosis_deeper_step_has_back_link_to_previous_question(self) -> None:
        response = self.client.get(
            "/printers/2/diagnosis"
            "?category=filament_not_sticking"
            "&node=q_filament_not_sticking_2"
            "&trail=No"
            "&path=q_filament_not_sticking_1"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            b'href="/printers/2/diagnosis?category=filament_not_sticking&amp;node=q_filament_not_sticking_1"',
            response.data,
        )

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
