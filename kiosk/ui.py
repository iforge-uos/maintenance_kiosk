"""Original Raspberry Pi kiosk screens backed by the durable kiosk service."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import math
import re
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from markupsafe import Markup, escape

from .domain import ACTIVE_INCIDENTS

SOP_DIR = Path(__file__).resolve().parent.parent / "sops"
STATE_LABELS = {"open": "Open", "in_progress": "In Progress", "needs_parts": "Needs parts", "escalated": "Escalated", "resolved": "Resolved", "closed_unresolved": "Closed unresolved", "draft": "Continue later", "completed": "Complete"}
AVAILABILITY_LABELS = {"available": "Available", "unavailable": "Unavailable", "under_maintenance": "Under Maintenance"}

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

def register_ui(app, service):
    ui = Blueprint("ui", __name__)

    def settings():
        return service.get_settings()

    def date_label(value):
        if not value:
            return "Never"
        try:
            return datetime.fromisoformat(value).astimezone(ZoneInfo(settings()["general"]["timezone"])).strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            return str(value)

    def printer_records(printer_id, kind=None):
        return [record for record in service.records(kind) if record["printer_id"] == printer_id]

    def active_incident(printer_id):
        return next((record for record in printer_records(printer_id, "reactive") if record["state"] in ACTIVE_INCIDENTS), None)

    def printer_view(printer):
        incident = active_incident(printer["id"])
        availability = AVAILABILITY_LABELS[printer["availability"]]
        weekly = sorted(printer_records(printer["id"], "weekly"), key=lambda record: record["created_at"], reverse=True)
        filament = weekly[0].get("metrics", {}).get("filament_m") if weekly else None
        used_km = round(max(float(filament or 0), 0) / 1000, 2)
        nozzle_percent = min(100, int(round(used_km / 3 * 100)))
        action_needed = STATE_LABELS.get((incident or {}).get("state"), "No action")
        if incident and incident.get("result_status") in {"Needs parts", "Temporary fix"}:
            action_needed = incident["result_status"]
        if nozzle_percent >= 100:
            action_needed = "Replace nozzle"
        return {**printer, "status": availability, "availability": availability,
                "reactive_state": action_needed if incident else "Clear", "has_open_fault": incident is not None,
                "action_needed": action_needed,
                "active_problem": printer["current_fault_summary"] or "No active problem",
                "recent_fault": printer["current_fault_summary"] or printer["latest_fix_summary"] or "No fault recorded",
                "last_weekly_at": date_label(printer["last_weekly_at"]),
                "last_reactive_at": date_label(printer["last_reactive_at"]),
                "last_fix_summary": printer["latest_fix_summary"] or "None",
                "nozzle_life_percent": nozzle_percent, "nozzle_life_label": f"{used_km:.2f} / 3 km" if filament is not None else "Not recorded"}

    def find_printer(printer_id):
        printer = next((p for p in service.printer_summaries() if p["id"] == printer_id and p["active"]), None)
        if printer is None:
            abort(404)
        return printer_view(printer)

    def categories():
        return sorted(service.get_diagnosis().get("categories", {}).items(), key=lambda item: item[1].get("display_order", 99))

    def technicians():
        return [item["name"] for item in settings()["technicians"] if item["active"]]

    def dashboard_url(printer_id=None):
        active = [p["id"] for p in settings()["printers"] if p["active"]]
        page = active.index(printer_id) // 4 + 1 if printer_id in active else 1
        return url_for("ui.dashboard", **({"page": page} if page > 1 else {}))

    def render(name, **context):
        context.setdefault("form", request.form if request.method == "POST" else {})
        context.setdefault("dashboard_url", dashboard_url((context.get("printer") or {}).get("id")))
        context.setdefault("back_url", context["dashboard_url"])
        return render_template(f"original/{name}.html", **context)

    def retaining(options, current):
        """Keep saved values selectable after configuration or workflow changes."""
        return list(options) + ([current] if current and current not in options else [])

    def symptom_options(form):
        return retaining([c["label"] for _, c in categories()] + ["Unknown / Other"], form.get("symptom_category"))

    def integer(value, label):
        try:
            return int(value)
        except (ValueError, TypeError):
            raise ValueError(f"Choose a valid {label}.") from None

    def identity(record=None):
        record_id = request.form.get("record_id", "")
        revision = request.form.get("revision", "")
        payload = {}
        if record_id:
            payload["id"] = record_id
            if not revision:
                raise ValueError("Reload this record before saving.")
            payload["revision"] = integer(revision, "revision")
        elif record:
            # A new form must not silently overwrite a record created in another session.
            raise ValueError("A record was created in another session. Reload before saving.")
        return payload

    def technician():
        chosen = request.form.get("technician_name", "")
        return request.form.get("other_technician", "").strip() if chosen == "Other" else chosen

    def history_events(printer_id):
        return [{"type": r["kind"], "at": date_label(r["updated_at"]), "technician": r.get("technician") or "Unassigned",
                 "summary": (f"Week {r['week']} maintenance" if r["kind"] == "weekly" else r.get("summary") or r.get("symptom") or "Maintenance incident"),
                 "state": STATE_LABELS[r["state"]], "open": r["state"] in ACTIVE_INCIDENTS or r["state"] == "draft"}
                for r in printer_records(printer_id)]

    def dashboard_payload():
        printers = [printer_view(p) for p in service.printer_summaries() if p["active"]]
        pages = max(1, math.ceil(len(printers) / 4))
        page = min(max(request.args.get("page", 1, type=int), 1), pages)
        google = settings()["google"]
        sync_jobs = [job for job in service.jobs() if job["kind"] == "sheets"]
        sync = "Offline" if not google["enabled"] else "Pending" if any(j["status"] in {"pending", "failed"} for j in sync_jobs) else "Synced" if sync_jobs else "Ready"
        return {"printers": printers[(page - 1) * 4:page * 4], "total_printers": len(printers), "current_page": page, "total_pages": pages,
                "unresolved_count": sum(p["open_incident_id"] is not None for p in printers), "sync_status": sync,
                "farm_name": settings()["general"]["farm_name"], "refresh_seconds": settings()["general"]["refresh_seconds"],
                "generated_at": datetime.now(ZoneInfo(settings()["general"]["timezone"])).strftime("%H:%M")}

    @ui.get("/")
    @ui.get("/dashboard")
    def dashboard():
        payload = dashboard_payload()
        return render("dashboard", payload=payload, back_url=url_for("ui.dashboard", page=max(1, payload["current_page"] - 1)))

    @ui.get("/api/dashboard")
    def dashboard_api():
        return jsonify(dashboard_payload())

    @ui.get("/weekly")
    @ui.get("/reactive")
    @ui.get("/history")
    @ui.get("/diagnosis")
    def legacy_tab():
        printer_id = request.args.get("printer") or request.args.get("printer_id")
        if not printer_id:
            printer_id = next((p["id"] for p in settings()["printers"] if p["active"]), None)
        if not printer_id:
            return redirect(url_for("ui.dashboard"))
        find_printer(printer_id)
        endpoint = request.path.strip("/")
        if endpoint == "reactive":
            endpoint = "reactive_start"
        return redirect(url_for("ui." + endpoint, printer_id=printer_id))

    @ui.get("/printers/<printer_id>")
    def printer_detail(printer_id):
        return render("printer_detail", printer=find_printer(printer_id), quick_fixes=QUICK_FIXES,
                      categories=categories(), recent_history=history_events(printer_id)[:3])

    def weekly_context(printer_id):
        schedule = settings()["schedule"]
        record_id = request.args.get("record")
        records = printer_records(printer_id, "weekly")
        record = next((r for r in records if r["id"] == record_id), None) if record_id else next((r for r in records if
            (r["academic_year"], r["semester"], r["week"]) == (schedule["academic_year"], schedule["semester"], schedule["week"])), None)
        if request.args.get("new") == "1" and not record_id:
            record = None
        if record_id and record is None:
            abort(404)
        checklist = (record or {}).get("checklist_definitions", settings()["checklist"])
        metrics = (record or {}).get("metric_definitions", settings()["metrics"])
        record = record or {}
        form = {"record_id": record.get("id", ""), "revision": record.get("revision", ""),
                "technician_name": record.get("technician", ""), "semester": record.get("semester", schedule["semester"]),
                "week_number": record.get("week", schedule["week"]), "note": record.get("note", ""),
                "academic_year": record.get("academic_year", schedule["academic_year"]), **record.get("metrics", {}), **record.get("checklist", {})}
        if form["technician_name"] and form["technician_name"] not in technicians():
            form["other_technician"], form["technician_name"] = form["technician_name"], "Other"
        return {"printer": find_printer(printer_id), "checklist_items": [(i["id"], i["label"]) for i in checklist if i["active"]],
                "metric_fields": [(i["id"], i["label"], i["unit"]) for i in metrics if i["active"]], "technicians": technicians(),
                "semesters": [{"id": n, "label": f"Semester {n}", "date_range": form["academic_year"]} for n in range(1, 5)],
                "weeks": range(1, max(schedule["teaching_weeks"], form["week_number"]) + 1), "teaching_weeks": schedule["teaching_weeks"],
                "form": form, "record_id": record.get("id"), "revision": record.get("revision"), "back_url": url_for("ui.printer_detail", printer_id=printer_id)}

    @ui.route("/printers/<printer_id>/weekly", methods=["GET", "POST"])
    def weekly(printer_id):
        context = weekly_context(printer_id)
        if request.method == "POST":
            try:
                payload = {"printer_id": printer_id, "technician": technician(), "academic_year": request.form.get("academic_year", settings()["schedule"]["academic_year"]),
                           "semester": integer(request.form.get("semester"), "semester"), "week": integer(request.form.get("week_number"), "week"),
                           "checklist": {key: key in request.form for key, _ in context["checklist_items"]}, "metrics": {},
                           "note": request.form.get("note", ""), "state": "completed" if request.form.get("save_action") == "complete" else "draft"}
                matching = next((r for r in printer_records(printer_id, "weekly") if (r["academic_year"], r["semester"], r["week"]) == (payload["academic_year"], payload["semester"], payload["week"])), None)
                payload.update(identity(matching))
                for key, label, _ in context["metric_fields"]:
                    value = request.form.get(key, "").strip()
                    if value:
                        try:
                            payload["metrics"][key] = float(value)
                        except ValueError:
                            raise ValueError(f"Enter a number for {label}.") from None
                service.save_weekly(payload)
                return redirect(url_for("ui.weekly_history", printer_id=printer_id))
            except ValueError as error:
                return render("weekly", **{**context, "form": request.form, "error": str(error)}), 400
        return render("weekly", **context)

    def weekly_view(record):
        checks = {i["label"]: record["checklist"].get(i["id"], False) for i in record["checklist_definitions"] if i["active"]}
        metrics = {i["label"]: f"{record['metrics'][i['id']]} {i['unit']}" for i in record["metric_definitions"] if i["id"] in record["metrics"]}
        return {**record, "event_id": record["id"], "week_number": record["week"], "semester": f"Semester {record['semester']}",
                "date": date_label(record["updated_at"]), "checks_complete": sum(checks.values()), "total_checks": len(checks),
                "checklist": checks, "metrics": metrics}

    @ui.get("/printers/<printer_id>/weekly/history")
    def weekly_history(printer_id):
        return render("weekly_history", printer=find_printer(printer_id), records=[weekly_view(r) for r in printer_records(printer_id, "weekly")], back_url=url_for("ui.printer_detail", printer_id=printer_id))

    @ui.get("/printers/<printer_id>/weekly/history/<event_id>")
    def weekly_detail(printer_id, event_id):
        printer = find_printer(printer_id)
        record = next((r for r in printer_records(printer_id, "weekly") if r["id"] == event_id), None)
        if record is None:
            abort(404)
        return render("weekly_detail", printer=printer, record=weekly_view(record), back_url=url_for("ui.weekly_history", printer_id=printer_id))

    def incident_form(printer_id):
        record = active_incident(printer_id) or {}
        name = record.get("technician", "")
        return {"record_id": record.get("id", ""), "revision": record.get("revision", ""), "technician_name": name if name in technicians() or not name else "Other", "other_technician": name if name not in technicians() else "",
                "event_origin": record.get("source", "User reported issue"), "symptom_category": record.get("symptom", ""),
                "issue_summary": record.get("summary", ""), "current_fault": record.get("summary", ""), "component_involved": record.get("component", ""),
                "action_taken": record.get("action_taken", ""), "result_status": record.get("result_status") or "Still unavailable", "fix_summary": record.get("fix_summary", ""), "note": record.get("note", ""),
                "urgency_state": AVAILABILITY_LABELS.get(record.get("availability"), "Unavailable")}

    @ui.get("/printers/<printer_id>/reactive")
    def reactive_entry(printer_id):
        find_printer(printer_id)
        return redirect(url_for("ui.reactive_start", printer_id=printer_id))

    @ui.route("/printers/<printer_id>/reactive/start", methods=["GET", "POST"])
    def reactive_start(printer_id):
        form = incident_form(printer_id)
        context = {"printer": find_printer(printer_id), "event_origins": retaining(EVENT_ORIGINS, form["event_origin"]), "urgency_states": URGENCY_STATES,
                   "categories": categories(), "form": form, "back_url": url_for("ui.printer_detail", printer_id=printer_id),
                   "preserved_symptom": form["symptom_category"] if form["symptom_category"] not in {value for key, category in categories() for value in (key, category["label"])} else ""}
        if request.method == "POST":
            action = request.form.get("next_action")
            if action == "cancel":
                return redirect(context["back_url"])
            try:
                if action not in {"diagnosis", "manual"}:
                    raise ValueError("Choose diagnosis or manual log.")
                category = request.form.get("symptom_category", "")
                category_data = dict(categories()).get(category)
                previous = active_incident(printer_id)
                if category_data is None and not (previous and category == previous["symptom"] and action == "manual"):
                    raise ValueError("Choose a symptom category.")
                summary = request.form.get("issue_summary", "").strip()
                if not summary:
                    raise ValueError("Enter a short issue summary.")
                service.save_incident({"printer_id": printer_id, **identity(active_incident(printer_id)), "summary": summary,
                    "symptom": category_data["label"] if category_data else category, "source": request.form.get("event_origin", "kiosk"), "note": request.form.get("note", ""),
                    "availability": "under_maintenance" if request.form.get("urgency_state") == "Under Maintenance" else "unavailable"})
                return redirect(url_for("ui.diagnosis" if action == "diagnosis" else "ui.manual_log", printer_id=printer_id, **({"category": category} if action == "diagnosis" else {})))
            except ValueError as error:
                return render("reactive_start", **{**context, "form": request.form, "error": str(error)}), 400
        return render("reactive_start", **context)

    def save_outcome(printer_id, source, diagnosis_node=None):
        form = request.form
        result = form.get("result_status", "Still unavailable")
        action = form.get("save_action")
        state = {"Fixed and available": "resolved", "Temporary fix": "in_progress", "Still unavailable": "open", "Needs parts": "in_progress", "Escalated": "escalated"}.get(result)
        if state is None:
            raise ValueError("Choose a result status.")
        if action == "available":
            state = "resolved"
        elif action == "progress":
            state = "in_progress"
        elif action == "unavailable" and state == "resolved":
            state = "open"
        payload = {"printer_id": printer_id, **identity(active_incident(printer_id)), "technician": technician(), "source": source,
                   "symptom": form.get("symptom_category", ""), "summary": form.get("current_fault", form.get("issue_summary", "")),
                   "action_taken": ", ".join(form.getlist("action_taken")), "component": form.get("component_involved", ""),
                   "state": state, "result_status": result, "fix_summary": form.get("fix_summary", ""), "note": form.get("note", (active_incident(printer_id) or {}).get("note", "")),
                   "availability": "available" if state == "resolved" else "under_maintenance" if action == "progress" else "unavailable"}
        if not payload["summary"].strip():
            raise ValueError("Enter an issue summary.")
        if diagnosis_node:
            node = service.get_diagnosis()["nodes"].get(diagnosis_node)
            if not node or node["type"] != "diagnosis":
                raise ValueError("Choose a diagnosis result.")
            path = [item for item in request.args.get("path", "").split("|") if item]
            if any(item not in service.get_diagnosis()["nodes"] for item in path):
                raise ValueError("The diagnosis path is invalid.")
            payload.update(diagnosis_path=path + [diagnosis_node], sop_ids=node.get("sop_ids", []))
        return service.save_incident(payload)

    @ui.route("/printers/<printer_id>/reactive/manual", methods=["GET", "POST"])
    def manual_log(printer_id):
        form = incident_form(printer_id)
        action_options = list(ACTION_TAKEN_OPTIONS)
        for action in form["action_taken"].split(", "):
            action_options = retaining(action_options, action)
        context = {"printer": find_printer(printer_id), "technicians": technicians(), "event_origins": retaining(EVENT_ORIGINS, form["event_origin"]),
                   "symptom_categories": symptom_options(form), "result_statuses": RESULT_STATUSES,
                   "action_options": action_options, "form": form, "back_url": url_for("ui.printer_detail", printer_id=printer_id)}
        if request.method == "POST":
            try:
                save_outcome(printer_id, request.form.get("event_origin", "manual"))
                return redirect(url_for("ui.history", printer_id=printer_id, filter="reactive"))
            except ValueError as error:
                return render("manual_log", **{**context, "form": request.form, "error": str(error)}), 400
        return render("manual_log", **context)

    @ui.route("/printers/<printer_id>/reactive/summary", methods=["GET", "POST"])
    def reactive_summary(printer_id):
        printer = find_printer(printer_id)
        quick_fix = next((fix for fix in QUICK_FIXES if fix["id"] == request.args.get("quick_fix")), None)
        node_id = request.args.get("diagnosis_node")
        node = service.get_diagnosis()["nodes"].get(node_id) if node_id else None
        if node_id and (not node or node["type"] != "diagnosis"):
            abort(404)
        form = incident_form(printer_id)
        diagnosis_symptom = form["symptom_category"]
        if node:
            tree = service.get_diagnosis()
            preferred_category = request.args.get("category")
            path = request.args.get("path", "").split("|")
            ordered = sorted(categories(), key=lambda item: (item[0] != preferred_category, item[1]["start_node"] not in path))
            for _, category in ordered:
                pending, visited = [category["start_node"]], set()
                while pending:
                    candidate = pending.pop()
                    if candidate in visited:
                        continue
                    visited.add(candidate)
                    pending.extend(option["next"] for option in tree["nodes"][candidate].get("options", []))
                if node_id in visited:
                    diagnosis_symptom = category["label"]
                    break
        diagnosis_prefill = None if not node else {"current_fault": node.get("label", ""), "symptom_category": diagnosis_symptom, "action_taken": ", ".join(node.get("recommended_actions", [])), "component_involved": node.get("component_involved", ""), "result_status": "Still unavailable", "fix_summary": node.get("fix_summary", ""), "diagnosis_label": node.get("label", ""), "sop_ids": node.get("sop_ids", [])}
        form.update(quick_fix or diagnosis_prefill or {})
        context = {"printer": printer, "technicians": technicians(), "symptom_categories": symptom_options(form),
                   "result_statuses": RESULT_STATUSES, "quick_fix": quick_fix, "diagnosis_prefill": diagnosis_prefill, "form": form,
                   "back_url": url_for("ui.printer_detail", printer_id=printer_id)}
        if request.method == "POST":
            try:
                save_outcome(printer_id, "quick_fix" if quick_fix else "diagnosis_assistant" if node else (active_incident(printer_id) or {}).get("source", "kiosk"), node_id)
                return redirect(dashboard_url(printer_id))
            except ValueError as error:
                return render("reactive", **{**context, "form": request.form, "error": str(error)}), 400
        return render("reactive", **context)

    @ui.get("/printers/<printer_id>/diagnosis")
    def diagnosis(printer_id):
        printer = find_printer(printer_id)
        tree = service.get_diagnosis()
        category_id = request.args.get("category")
        path = [item for item in request.args.get("path", "").split("|") if item]
        trail = [item for item in request.args.get("trail", "").split("|") if item]
        category = tree["categories"].get(category_id) if category_id else None
        if category_id and not category:
            abort(404)
        node_id = request.args.get("node") or (category or {}).get("start_node")
        node = tree["nodes"].get(node_id) if node_id else None
        if category and not node:
            abort(404)
        if any(item not in tree["nodes"] for item in path) or len(path) > 100:
            abort(400)
        back = url_for("ui.printer_detail", printer_id=printer_id)
        if category:
            back = url_for("ui.diagnosis", printer_id=printer_id)
        if path:
            back = url_for("ui.diagnosis", printer_id=printer_id, category=category_id, node=path[-1], path="|".join(path[:-1]), trail="|".join(trail[:-1]))
        return render("diagnosis", printer=printer, categories=categories(), selected_category=(category_id, category) if category else None,
                      node=node, node_id=node_id, trail=trail, path=path, back_url=back, sops=tree["sops"])

    @ui.get("/printers/<printer_id>/history")
    def history(printer_id):
        printer = find_printer(printer_id)
        filter_type = request.args.get("filter", "all")
        events = history_events(printer_id)
        if filter_type in {"weekly", "reactive"}:
            events = [event for event in events if event["type"] == filter_type]
        elif filter_type == "open":
            events = [event for event in events if event["open"]]
        return render("history", printer=printer, events=events, filter_type=filter_type, back_url=url_for("ui.printer_detail", printer_id=printer_id))

    @ui.get("/sop-assets/<slug>/<path:filename>")
    def sop_asset(slug, filename):
        from flask import send_from_directory
        folder = safe_path(slug)
        if not folder.is_dir():
            abort(404)
        return send_from_directory(folder, filename)

    @ui.get("/sops/<slug>")
    def sop(slug):
        candidate = safe_path(slug)
        candidates = sorted(candidate.glob("*.html")) if candidate.is_dir() else []
        candidates += [safe_path(slug + ".html"), safe_path(slug + ".md")]
        metadata = service.get_diagnosis()["sops"].get(slug)
        document_url = None
        if metadata and metadata.get("file"):
            linked = str(metadata["file"])
            parsed = urlparse(linked)
            if parsed.scheme == "https" and parsed.netloc:
                document_url = linked
            else:
                if linked.startswith("/sops/"):
                    linked = linked[1:]
                if linked.startswith("sops/"):
                    linked = linked[5:]
                if Path(linked).suffix in {".html", ".md"}:
                    candidates.insert(0, safe_path(linked))
                elif Path(linked).suffix.lower() == ".pdf" and safe_path(linked).is_file():
                    document_url = url_for("ui.sop_document", filename=linked)
        path = next((p for p in candidates if p.is_file() and p.suffix in {".html", ".md"}), None)
        if document_url:
            content = markdown_to_html(f"# {metadata.get('title', slug)}\n\n{metadata.get('description', '')}")
            content += Markup('<p><a href="{}" rel="noopener noreferrer">Open linked instructions</a></p>').format(document_url)
        elif path:
            content = html_sop(path) if path.suffix == ".html" else markdown_to_html(path.read_text())
        elif metadata:
            content = markdown_to_html(f"# {metadata.get('title', slug)}\n\n{metadata.get('description', '')}\n\nThe linked instruction file is not installed on this kiosk.")
        else:
            abort(404)
        printer_id = request.args.get("printer")
        back = url_for("ui.diagnosis", printer_id=printer_id) if printer_id else url_for("ui.dashboard")
        return render("sop", slug=slug, content=content, back_url=back, dashboard_url=dashboard_url(printer_id))

    @ui.get("/sop-documents/<path:filename>")
    def sop_document(filename):
        from flask import send_from_directory
        path = safe_path(filename)
        if path.suffix.lower() != ".pdf" or not path.is_file():
            abort(404)
        return send_from_directory(SOP_DIR, filename, mimetype="application/pdf")

    app.register_blueprint(ui)


def safe_path(relative):
    candidate = (SOP_DIR / relative).resolve()
    if not candidate.is_relative_to(SOP_DIR.resolve()) or candidate == SOP_DIR.resolve():
        abort(404)
    return candidate


def html_sop(path):
    html = path.read_text(encoding="utf-8")
    body = re.search(r"<body\b[^>]*>(.*?)</body>", html, re.IGNORECASE | re.DOTALL)
    body = body.group(1) if body else html
    styles = re.findall(r"<style\b[^>]*>(.*?)</style>", html, re.IGNORECASE | re.DOTALL)
    scoped = []
    for stylesheet in styles:
        for rule in stylesheet.split("}"):
            if "{" in rule:
                selectors, declarations = rule.split("{", 1)
                selectors = ", ".join(".sop-html-content " + item.strip() for item in selectors.split(",") if item.strip())
                scoped.append(f"{selectors} {{{declarations}}}")
    def image_url(match):
        src = match.group(2)
        if not src.startswith(("/", "http:", "https:", "data:")):
            src = url_for("ui.sop_asset", slug=path.parent.name, filename=src)
        return match.group(1) + str(escape(src)) + match.group(3)
    body = re.sub(r'(<img\b[^>]*\bsrc=[\"\'])([^\"\']+)([\"\'])', image_url, body, flags=re.IGNORECASE)
    return Markup('<style>' + "\n".join(scoped) + '</style><div class="sop-html-content">' + body + '</div>')


def markdown_to_html(markdown):
    lines = []
    in_list = False
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line.startswith("- ") and in_list:
            lines.append("</ul>")
            in_list = False
        if line.startswith("- "):
            if not in_list:
                lines.append("<ul>")
                in_list = True
            lines.append(f"<li>{escape(line[2:])}</li>")
        elif line.startswith(("# ", "## ", "### ")):
            heading, value = line.split(" ", 1)
            lines.append(f"<h{len(heading)}>{escape(value)}</h{len(heading)}>")
        elif line:
            lines.append(f"<p>{escape(line)}</p>")
    if in_list:
        lines.append("</ul>")
    return Markup("\n".join(lines))
