from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import backend
from flask import Flask, abort, jsonify, redirect, render_template, request, url_for
from markupsafe import Markup, escape


app = Flask(__name__)
app.config["SECRET_KEY"] = "dev-kiosk-secret"


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SOP_DIR = BASE_DIR / "sops"
DIAGNOSIS_TREE_PATH = DATA_DIR / "diagnosis_tree.json"


SEMESTERS = [
    {
        "id": "semester_1",
        "label": "Semester 1",
        "date_range": "29 Sep 2025 - 20 Dec 2025",
    },
    {
        "id": "semester_2",
        "label": "Semester 2",
        "date_range": "9 Feb 2026 - 13 Jun 2026",
    },
]

TECHNICIANS = [f"Technician {number}" for number in range(1, 9)]
DASHBOARD_PAGE_SIZE = 10

CHECKLIST_ITEMS = [
    ("nozzle_debris_brushed", "Brush off nozzle debris"),
    ("wiper_screw_tightened", "Tighten wiper screw"),
    ("fan_screw_tightened", "Tighten fan screw"),
    ("enclosure_debris_cleaned", "Clean enclosure debris"),
    ("bed_cleaned", "Clean bed"),
    ("glue_reapplied", "Reapply glue"),
    ("enclosure_fan_ok", "KORA enclosure fan function"),
    ("filament_sensor_turned_on", "Filament sensor turned on"),
]

METRIC_FIELDS = [
    ("x_movement_km", "X move", "km"),
    ("y_movement_km", "Y move", "km"),
    ("z_movement_m", "Z move", "m"),
    ("filament_m", "Filament", "m"),
    ("total_print_hours", "Print hrs", "h"),
]

SYMPTOM_CATEGORIES = [
    "Filament not sticking",
    "Filament not extruding",
    "Under extrusion",
    "Prints layer shift",
    "Bed leveling fail",
    "Unable to change filament",
    "Fan not spinning",
    "Unknown / Other",
]

EVENT_ORIGINS = [
    "Button-triggered fault",
    "User reported issue",
    "Technician observed issue",
    "Print failure observed",
    "Other",
]

URGENCY_STATES = [
    "Unavailable",
    "Under Maintenance",
    "Intermittent issue",
    "Investigation only",
]

RESULT_STATUSES = [
    "Fixed and available",
    "Temporary fix",
    "Still unavailable",
    "Needs parts",
    "Escalated",
]

ACTION_TAKEN_OPTIONS = [
    "Cleaned bed",
    "Cleaned nozzle",
    "Re-leveled bed",
    "Cleared filament path",
    "Checked fan",
    "Tightened screw",
    "Escalated",
]

QUICK_FIXES = [
    {
        "id": "nozzle-clog",
        "label": "Nozzle clog fixed",
        "symptom_category": "Filament not extruding",
        "current_fault": "Extrusion stopped during print",
        "action_taken": "Cleared nozzle clog and purged filament",
        "component_involved": "Nozzle / hotend",
        "result_status": "Fixed and available",
        "fix_summary": "Nozzle clog cleared",
        "sop_slug": "nozzle-clog",
    },
    {
        "id": "bed-relevel",
        "label": "Bed re-leveled",
        "symptom_category": "Filament not sticking",
        "current_fault": "First layer adhesion failure",
        "action_taken": "Cleaned bed and re-leveled printer",
        "component_involved": "Build plate / Z offset",
        "result_status": "Fixed and available",
        "fix_summary": "Bed re-leveled",
        "sop_slug": "bed-adhesion",
    },
    {
        "id": "fan-clear",
        "label": "Fan debris cleared",
        "symptom_category": "Fan not spinning",
        "current_fault": "Fan blocked by debris",
        "action_taken": "Removed debris and confirmed fan spins",
        "component_involved": "Fan",
        "result_status": "Fixed and available",
        "fix_summary": "Fan debris cleared",
        "sop_slug": "fan-check",
    },
]

PRINTERS = [
    {
        "id": 1,
        "name": "Beethoven",
        "status": "Unavailable",
        "reactive_state": "Needs parts",
        "current_fault_summary": "Hotend fan fault - awaiting replacement",
        "last_weekly_at": "2026-04-27",
        "last_reactive_at": "2026-05-03",
        "last_fix_summary": "Extruder gear cleaned",
        "has_open_fault": True,
    },
    {
        "id": 2,
        "name": "Picasso",
        "status": "Available",
        "reactive_state": "Resolved",
        "current_fault_summary": "",
        "last_weekly_at": "2026-04-29",
        "last_reactive_at": "2026-04-25",
        "last_fix_summary": "Bed re-leveled",
        "has_open_fault": False,
    },
    {
        "id": 3,
        "name": "Hypatia",
        "status": "Under Maintenance",
        "reactive_state": "In Progress",
        "current_fault_summary": "Layer shift check in progress",
        "last_weekly_at": "2026-04-28",
        "last_reactive_at": "2026-05-04",
        "last_fix_summary": "Belt tension inspected",
        "has_open_fault": True,
    },
    {
        "id": 4,
        "name": "Watt",
        "status": "Available",
        "reactive_state": "Clear",
        "current_fault_summary": "",
        "last_weekly_at": "2026-04-30",
        "last_reactive_at": "2026-04-18",
        "last_fix_summary": "Nozzle clog cleared",
        "has_open_fault": False,
    },
    {
        "id": 5,
        "name": "Bell",
        "status": "Unavailable",
        "reactive_state": "Escalated",
        "current_fault_summary": "Filament change mechanism jammed",
        "last_weekly_at": "2026-04-24",
        "last_reactive_at": "2026-05-02",
        "last_fix_summary": "Filament path cleaned",
        "has_open_fault": True,
    },
    {
        "id": 6,
        "name": "Tolstoy",
        "status": "Available",
        "reactive_state": "Clear",
        "current_fault_summary": "",
        "last_weekly_at": "2026-04-26",
        "last_reactive_at": "2026-04-20",
        "last_fix_summary": "Glue reapplied",
        "has_open_fault": False,
    },
    {
        "id": 7,
        "name": "Socrates",
        "status": "Available",
        "reactive_state": "Clear",
        "current_fault_summary": "",
        "last_weekly_at": "2026-04-30",
        "last_reactive_at": "2026-04-14",
        "last_fix_summary": "Wiper screw tightened",
        "has_open_fault": False,
    },
    {
        "id": 8,
        "name": "Einstein",
        "status": "Under Maintenance",
        "reactive_state": "Temporary fix",
        "current_fault_summary": "Intermittent under-extrusion",
        "last_weekly_at": "2026-04-28",
        "last_reactive_at": "2026-05-01",
        "last_fix_summary": "Partial clog cleared",
        "has_open_fault": True,
    },
    {
        "id": 9,
        "name": "Rosalind",
        "status": "Available",
        "reactive_state": "Clear",
        "current_fault_summary": "",
        "last_weekly_at": "2026-04-29",
        "last_reactive_at": "2026-04-16",
        "last_fix_summary": "Bed cleaned",
        "has_open_fault": False,
    },
    {
        "id": 10,
        "name": "H2D",
        "status": "Available",
        "reactive_state": "Clear",
        "current_fault_summary": "",
        "last_weekly_at": "2026-04-25",
        "last_reactive_at": "2026-04-12",
        "last_fix_summary": "Fan checked",
        "has_open_fault": False,
    },
]

HISTORY = {
    1: [
        {
            "type": "reactive",
            "at": "2026-05-03 14:21",
            "technician": "Technician 2",
            "summary": "Hotend fan fault - awaiting replacement",
            "state": "Needs parts",
        },
        {
            "type": "weekly",
            "at": "2026-04-27 09:35",
            "technician": "Technician 1",
            "summary": "Week 12 maintenance complete, 7/7 checks",
            "state": "Complete",
        },
        {
            "type": "reactive",
            "at": "2026-04-22 15:10",
            "technician": "Technician 4",
            "summary": "Extruder gear cleaned",
            "state": "Resolved",
        },
    ],
    3: [
        {
            "type": "reactive",
            "at": "2026-05-04 10:18",
            "technician": "Technician 3",
            "summary": "Layer shift check in progress",
            "state": "In Progress",
        },
        {
            "type": "weekly",
            "at": "2026-04-28 11:02",
            "technician": "Technician 5",
            "summary": "Week 12 maintenance complete, 6/7 checks",
            "state": "Complete",
        },
    ],
    5: [
        {
            "type": "reactive",
            "at": "2026-05-02 16:44",
            "technician": "Technician 6",
            "summary": "Filament change mechanism jammed",
            "state": "Escalated",
        },
        {
            "type": "weekly",
            "at": "2026-04-24 09:20",
            "technician": "Technician 2",
            "summary": "Week 12 maintenance complete, 7/7 checks",
            "state": "Complete",
        },
    ],
    8: [
        {
            "type": "reactive",
            "at": "2026-05-01 13:05",
            "technician": "Technician 8",
            "summary": "Intermittent under-extrusion",
            "state": "Temporary fix",
        },
        {
            "type": "weekly",
            "at": "2026-04-28 08:58",
            "technician": "Technician 7",
            "summary": "Week 12 maintenance complete, 7/7 checks",
            "state": "Complete",
        },
    ],
}

WEEKLY_RECORDS = {
    1: [
        {
            "event_id": "w-1-12",
            "academic_year": "2025/26",
            "semester": "Semester 2",
            "week_number": 12,
            "date": "2026-04-27 09:35",
            "technician": "Technician 1",
            "checks_complete": 7,
            "total_checks": 7,
            "note": "No issues found during weekly maintenance.",
            "checklist": {
                "Brush off nozzle debris": True,
                "Tighten wiper screw": True,
                "Tighten fan screw": True,
                "Clean enclosure debris": True,
                "Clean bed": True,
                "Reapply glue": True,
                "KORA enclosure fan function": True,
                "Filament sensor turned on": True,
            },
            "metrics": {
                "X movement": "42.8 km",
                "Y movement": "40.4 km",
                "Z movement": "88 m",
                "Filament": "1180 m",
                "Total print hours": "693 h",
            },
        }
    ],
    3: [
        {
            "event_id": "w-3-12",
            "academic_year": "2025/26",
            "semester": "Semester 2",
            "week_number": 12,
            "date": "2026-04-28 11:02",
            "technician": "Technician 5",
            "checks_complete": 6,
            "total_checks": 7,
            "note": "Fan function needs a follow-up check.",
            "checklist": {
                "Brush off nozzle debris": True,
                "Tighten wiper screw": True,
                "Tighten fan screw": True,
                "Clean enclosure debris": True,
                "Clean bed": True,
                "Reapply glue": True,
                "KORA enclosure fan function": False,
                "Filament sensor turned on": True,
            },
            "metrics": {
                "X movement": "37.2 km",
                "Y movement": "35.1 km",
                "Z movement": "74 m",
                "Filament": "940 m",
                "Total print hours": "611 h",
            },
        }
    ],
    5: [
        {
            "event_id": "w-5-12",
            "academic_year": "2025/26",
            "semester": "Semester 2",
            "week_number": 12,
            "date": "2026-04-24 09:20",
            "technician": "Technician 2",
            "checks_complete": 7,
            "total_checks": 7,
            "note": "Minor wear observed on filament path.",
            "checklist": {
                "Brush off nozzle debris": True,
                "Tighten wiper screw": True,
                "Tighten fan screw": True,
                "Clean enclosure debris": True,
                "Clean bed": True,
                "Reapply glue": True,
                "KORA enclosure fan function": True,
                "Filament sensor turned on": True,
            },
            "metrics": {
                "X movement": "45.7 km",
                "Y movement": "43.2 km",
                "Z movement": "92 m",
                "Filament": "1264 m",
                "Total print hours": "730 h",
            },
        }
    ],
    8: [
        {
            "event_id": "w-8-12",
            "academic_year": "2025/26",
            "semester": "Semester 2",
            "week_number": 12,
            "date": "2026-04-28 08:58",
            "technician": "Technician 7",
            "checks_complete": 7,
            "total_checks": 7,
            "note": "No issues.",
            "checklist": {
                "Brush off nozzle debris": True,
                "Tighten wiper screw": True,
                "Tighten fan screw": True,
                "Clean enclosure debris": True,
                "Clean bed": True,
                "Reapply glue": True,
                "KORA enclosure fan function": True,
                "Filament sensor turned on": True,
            },
            "metrics": {
                "X movement": "39.9 km",
                "Y movement": "39.1 km",
                "Z movement": "81 m",
                "Filament": "1012 m",
                "Total print hours": "645 h",
            },
        }
    ],
}

def initialize_database() -> None:
    backend.init_database(PRINTERS, WEEKLY_RECORDS, HISTORY)


initialize_database()


def find_printer(printer_id: int) -> dict[str, Any]:
    printer = backend.find_printer(printer_id)
    if printer:
        return printer
    abort(404)


def load_diagnosis_tree() -> dict[str, Any]:
    if not DIAGNOSIS_TREE_PATH.exists():
        return {"categories": {}, "nodes": {}, "sops": {}}
    return json.loads(DIAGNOSIS_TREE_PATH.read_text())


def diagnosis_categories() -> list[tuple[str, dict[str, Any]]]:
    tree = load_diagnosis_tree()
    categories = tree.get("categories", {})
    return sorted(categories.items(), key=lambda item: item[1].get("display_order", 99))


def find_weekly_record(printer_id: int, event_id: str) -> dict[str, Any]:
    record = backend.find_weekly_record(printer_id, event_id)
    if record:
        return record
    abort(404)


def full_history_events(printer_id: int) -> list[dict[str, Any]]:
    return backend.full_history_events(printer_id)


def sop_lookup(slug: str) -> dict[str, Any] | None:
    tree = load_diagnosis_tree()
    sops = tree.get("sops", {})
    if isinstance(sops, dict):
        sop = sops.get(slug)
        if sop:
            return sop

    return None


def diagnosis_result_prefill(node: dict[str, Any] | None) -> dict[str, Any] | None:
    if not node or node.get("type") != "diagnosis":
        return None

    return {
        "current_fault": node.get("label", ""),
        "symptom_category": "",
        "action_taken": ", ".join(node.get("recommended_actions", [])),
        "component_involved": node.get("component_involved", ""),
        "result_status": "Still unavailable",
        "fix_summary": node.get("fix_summary", ""),
        "diagnosis_label": node.get("label", ""),
        "diagnosis_description": node.get("description", ""),
        "sop_ids": node.get("sop_ids", []),
    }


def get_dashboard_payload(page: int = 1) -> dict[str, Any]:
    return backend.get_dashboard_payload(page, DASHBOARD_PAGE_SIZE)


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


def markdown_to_html(markdown: str) -> Markup:
    html_lines = []
    in_list = False

    for raw_line in markdown.splitlines():
        line = raw_line.strip()

        if not line:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            continue

        if line.startswith("### "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h3>{escape(line[4:])}</h3>")
        elif line.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h2>{escape(line[3:])}</h2>")
        elif line.startswith("# "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h1>{escape(line[2:])}</h1>")
        elif line.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{escape(line[2:])}</li>")
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<p>{escape(line)}</p>")

    if in_list:
        html_lines.append("</ul>")

    return Markup("\n".join(html_lines))


@app.get("/")
def dashboard():
    page = request.args.get("page", 1, type=int)
    return render_template("dashboard.html", payload=get_dashboard_payload(page), quick_fixes=QUICK_FIXES)


@app.get("/api/dashboard")
def dashboard_api():
    page = request.args.get("page", 1, type=int)
    return jsonify(get_dashboard_payload(page))


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


@app.get("/printers/<int:printer_id>")
def printer_detail(printer_id: int):
    printer = find_printer(printer_id)
    weekly_records = backend.weekly_records(printer_id)
    recent_history = full_history_events(printer_id)[:3]

    return render_template(
        "printer_detail.html",
        printer=printer,
        quick_fixes=QUICK_FIXES,
        categories=diagnosis_categories(),
        weekly_records=weekly_records,
        recent_history=recent_history,
    )


@app.route("/printers/<int:printer_id>/weekly", methods=["GET", "POST"])
def weekly(printer_id: int):
    printer = find_printer(printer_id)
    if request.method == "POST":
        backend.save_weekly_maintenance(printer_id, request.form, CHECKLIST_ITEMS, METRIC_FIELDS, SEMESTERS)
        return redirect(url_for("weekly_history", printer_id=printer_id, saved="weekly"))

    return render_template(
        "weekly.html",
        printer=printer,
        checklist_items=CHECKLIST_ITEMS,
        metric_fields=METRIC_FIELDS,
        semesters=SEMESTERS,
        technicians=technician_names(),
        weeks=range(1, 14),
    )


@app.get("/printers/<int:printer_id>/weekly/history")
def weekly_history(printer_id: int):
    printer = find_printer(printer_id)
    records = backend.weekly_records(printer_id)
    return render_template("weekly_history.html", printer=printer, records=records)


@app.get("/printers/<int:printer_id>/weekly/history/<event_id>")
def weekly_detail(printer_id: int, event_id: str):
    printer = find_printer(printer_id)
    record = find_weekly_record(printer_id, event_id)
    return render_template("weekly_detail.html", printer=printer, record=record)


@app.get("/printers/<int:printer_id>/reactive")
def reactive_entry(printer_id: int):
    return redirect(url_for("reactive_start", printer_id=printer_id))


@app.route("/printers/<int:printer_id>/reactive/start", methods=["GET", "POST"])
def reactive_start(printer_id: int):
    printer = find_printer(printer_id)

    if request.method == "POST":
        action = request.form.get("next_action")
        category = request.form.get("symptom_category")
        if action == "diagnosis":
            backend.ensure_reactive_event(printer_id, request.form)
            return redirect(url_for("diagnosis", printer_id=printer_id, category=category))
        if action == "manual":
            backend.ensure_reactive_event(printer_id, request.form)
            return redirect(url_for("manual_log", printer_id=printer_id))
        return redirect(url_for("printer_detail", printer_id=printer_id))

    return render_template(
        "reactive_start.html",
        printer=printer,
        event_origins=EVENT_ORIGINS,
        urgency_states=URGENCY_STATES,
        categories=diagnosis_categories(),
    )


@app.get("/printers/<int:printer_id>/diagnosis")
def diagnosis(printer_id: int):
    printer = find_printer(printer_id)
    tree = load_diagnosis_tree()
    categories = diagnosis_categories()
    category_id = request.args.get("category")
    node_id = request.args.get("node")
    trail = [item for item in request.args.get("trail", "").split("|") if item]

    if not category_id:
        return render_template(
            "diagnosis.html",
            printer=printer,
            categories=categories,
            selected_category=None,
            node=None,
            node_id=None,
            trail=trail,
            sops={},
        )

    category = tree.get("categories", {}).get(category_id)
    if not category:
        abort(404)

    node_id = node_id or category.get("start_node")
    node = tree.get("nodes", {}).get(node_id)
    if not node:
        abort(404)

    return render_template(
        "diagnosis.html",
        printer=printer,
        categories=categories,
        selected_category=(category_id, category),
        node=node,
        node_id=node_id,
        trail=trail,
        sops=tree.get("sops", {}),
    )


@app.route("/printers/<int:printer_id>/reactive/manual", methods=["GET", "POST"])
def manual_log(printer_id: int):
    printer = find_printer(printer_id)

    if request.method == "POST":
        backend.save_reactive_event(printer_id, request.form, source="manual")
        return redirect(url_for("history", printer_id=printer_id, filter="reactive", saved="manual"))

    return render_template(
        "manual_log.html",
        printer=printer,
        technicians=technician_names(),
        event_origins=EVENT_ORIGINS,
        symptom_categories=SYMPTOM_CATEGORIES,
        result_statuses=RESULT_STATUSES,
        action_options=ACTION_TAKEN_OPTIONS,
    )


@app.route("/printers/<int:printer_id>/reactive/summary", methods=["GET", "POST"])
def reactive_summary(printer_id: int):
    printer = find_printer(printer_id)
    quick_fix_id = request.args.get("quick_fix")
    diagnosis_node_id = request.args.get("diagnosis_node")
    quick_fix = next((item for item in QUICK_FIXES if item["id"] == quick_fix_id), None)
    diagnosis_prefill = None

    if diagnosis_node_id:
        node = load_diagnosis_tree().get("nodes", {}).get(diagnosis_node_id)
        diagnosis_prefill = diagnosis_result_prefill(node)

    if request.method == "POST":
        source = "quick_fix" if quick_fix_id else "diagnosis_assistant" if diagnosis_node_id else request.args.get("source", "manual")
        backend.save_reactive_event(
            printer_id,
            request.form,
            source=source,
            save_action=request.form.get("save_action"),
            diagnosis_node_id=diagnosis_node_id,
        )
        return redirect(url_for("dashboard", saved="reactive", printer=printer_id))

    return render_template(
        "reactive.html",
        printer=printer,
        technicians=technician_names(),
        symptom_categories=SYMPTOM_CATEGORIES,
        result_statuses=RESULT_STATUSES,
        quick_fix=quick_fix,
        diagnosis_prefill=diagnosis_prefill,
    )


@app.get("/printers/<int:printer_id>/history")
def history(printer_id: int):
    printer = find_printer(printer_id)
    filter_type = request.args.get("filter", "all")
    events = full_history_events(printer_id)

    if filter_type == "weekly":
        events = [event for event in events if event["type"] == "weekly"]
    elif filter_type == "reactive":
        events = [event for event in events if event["type"] == "reactive"]
    elif filter_type == "open":
        events = [event for event in events if event["state"] in {"Needs parts", "Escalated", "In Progress", "Temporary fix"}]

    return render_template("history.html", printer=printer, events=events, filter_type=filter_type)


@app.get("/sops/<slug>")
def sop(slug: str):
    sop_path = SOP_DIR / f"{slug}.md"
    if sop_path.exists():
        return render_template("sop.html", slug=slug, content=markdown_to_html(sop_path.read_text()))

    sop_data = sop_lookup(slug)
    if sop_data:
        markdown = "\n".join(
            [
                f"# {sop_data.get('title', slug)}",
                "",
                sop_data.get("description", "No SOP description available yet."),
                "",
                "## Tags",
                *[f"- {tag}" for tag in sop_data.get("tags", [])],
                "",
                "## Source File",
                sop_data.get("file", "Markdown SOP file not linked yet."),
            ]
        )
        return render_template("sop.html", slug=slug, content=markdown_to_html(markdown))

    abort(404)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)
