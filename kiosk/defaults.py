"""Safe, offline defaults for a new kiosk installation."""

DEFAULT_SETTINGS = {
    "revision": 1,
    "general": {"farm_name": "Print Farm Maintenance Kiosk", "timezone": "Europe/London", "refresh_seconds": 30},
    "printers": [
        {"id": f"core-one-{n:02d}", "name": name, "model": "Bambu Lab H2D" if name == "H2D" else "Prusa Core One", "active": True}
        for n, name in enumerate(
            ["Bell", "Tolstoy", "Einstein", "Socrates", "Picasso", "Beethoven", "Hypatia", "Watt", "H2D", "Rosalind"], 1
        )
    ],
    "technicians": [{"id": f"technician-{n}", "name": f"Technician {n}", "active": True} for n in range(1, 9)],
    "checklist": [
        {"id": key, "label": label, "required": False, "active": True}
        for key, label in [
            ("nozzle_debris", "Brush off nozzle debris"),
            ("wiper_screw", "Tighten wiper screw"),
            ("fan_screw", "Tighten fan screw"),
            ("enclosure_cleaned", "Clean enclosure debris"),
            ("bed_cleaned", "Clean bed"),
            ("glue_applied", "Reapply glue"),
            ("enclosure_fan", "KORA enclosure fan function"),
            ("filament_sensor", "Filament sensor turned on"),
        ]
    ],
    "metrics": [
        {"id": key, "label": label, "unit": unit, "active": True}
        for key, label, unit in [
            ("x_movement_km", "X move", "km"),
            ("y_movement_km", "Y move", "km"),
            ("z_movement_m", "Z move", "m"),
            ("filament_m", "Filament", "m"),
            ("total_print_hours", "Print hrs", "h"),
        ]
    ],
    "schedule": {"academic_year": "2026/27", "semester": 1, "week": 1, "teaching_weeks": 13, "reminders_enabled": False},
    "google": {"enabled": False, "account_email": "threedprintingiforge@gmail.com", "auth_mode": "service_account", "spreadsheet_id": "", "folder_id": "", "sync_interval": 30},
    "email": {"enabled": False, "host": "", "port": 587, "security": "starttls", "username": "", "sender": "", "recipients": [], "events": ["fault_opened", "incident_resolved"], "subject_prefix": "[iForge]"},
    "discord": {"enabled": False, "destinations": [{"id": "maintenance", "name": "Maintenance alerts", "enabled": True, "events": ["fault_opened", "incident_resolved"], "printer_ids": []}]},
    "hardware": {"backend": "mock", "hold_seconds": 1, "debounce_seconds": 0.1, "mappings": [{"printer_id": "core-one-01", "button_pin": 17, "led_pin": 27, "pull_up": True, "led_active_high": True}]},
    "backups": {"enabled": True, "retention": 30},
}
