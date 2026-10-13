"""Exercise the restored touch-screen forms against the durable service."""
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest
from werkzeug.datastructures import MultiDict

from kiosk import create_app


PRINTER = "/printers/core-one-01"


class Page(HTMLParser):
    """Read links and successful form fields as a browser would submit them."""

    def __init__(self, response, status=200):
        super().__init__()
        assert response.status_code == status, response.get_data(as_text=True)
        self.elements = []
        self.fields = MultiDict()
        self.select = None
        self.option = None
        self.textarea = None
        self.feed(response.get_data(as_text=True))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append((tag, attrs))
        if tag == "input" and attrs.get("name") and "disabled" not in attrs:
            kind = attrs.get("type", "text")
            if kind in {"submit", "button"}:
                return
            if kind in {"checkbox", "radio"} and "checked" not in attrs:
                return
            self.fields.add(attrs["name"], attrs.get("value", "on" if kind == "checkbox" else ""))
        elif tag == "select":
            self.select = {"name": attrs.get("name"), "options": []}
        elif tag == "option" and self.select is not None:
            self.option = {"attrs": attrs, "text": ""}
        elif tag == "textarea":
            self.textarea = {"name": attrs.get("name"), "text": ""}

    def handle_data(self, data):
        if self.option is not None:
            self.option["text"] += data
        if self.textarea is not None:
            self.textarea["text"] += data

    def handle_endtag(self, tag):
        if tag == "option" and self.option is not None:
            self.select["options"].append(self.option)
            self.option = None
        elif tag == "select" and self.select is not None:
            options = self.select["options"]
            if options and self.select["name"]:
                selected = next((o for o in options if "selected" in o["attrs"]), options[0])
                self.fields.add(self.select["name"], selected["attrs"].get("value", selected["text"].strip()))
            self.select = None
        elif tag == "textarea" and self.textarea is not None:
            if self.textarea["name"]:
                self.fields.add(self.textarea["name"], self.textarea["text"])
            self.textarea = None

    @property
    def links(self):
        return [attrs["href"] for tag, attrs in self.elements if tag == "a" and "href" in attrs]

    @property
    def back_links(self):
        return [attrs["href"] for tag, attrs in self.elements if tag == "a" and "data-back" in attrs]


def overwrite(fields, values):
    for name, value in values.items():
        fields[name] = value


@pytest.fixture
def app(tmp_path):
    return create_app({"TESTING": True, "DATA_DIR": tmp_path})


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.mark.parametrize("route", [
    "/", "/dashboard", PRINTER, PRINTER + "/weekly", PRINTER + "/weekly/history",
    PRINTER + "/reactive/start", PRINTER + "/reactive/manual", PRINTER + "/reactive/summary",
    PRINTER + "/diagnosis", PRINTER + "/history", "/sops/nozzle-clog",
])
def test_screens_render_and_back_links_work(client, route):
    page = Page(client.get(route))
    assert page.back_links, route
    assert [attrs["href"] for tag, attrs in page.elements
            if tag == "a" and "data-home" in attrs] == ["/"]
    for target in page.back_links:
        assert client.get(target).status_code == 200, target
    assert client.get("/api/state").json["records"] == []


def test_dashboard_pages_and_settings_are_reachable(client):
    state = client.get("/api/state").json
    expected = [p["id"] for p in state["printers"] if p["active"]]
    seen = []
    for number in range(1, (len(expected) + 3) // 4 + 1):
        page = Page(client.get(f"/dashboard?page={number}"))
        cards = [attrs["data-printer-card"] for _, attrs in page.elements if "data-printer-card" in attrs]
        assert 1 <= len(cards) <= 4
        seen.extend(cards)
        polled = client.get(f"/api/dashboard?page={number}").json
        assert [p["id"] for p in polled["printers"]] == cards
        for printer_id in cards:
            target = f"/printers/{printer_id}"
            assert target in page.links
            assert client.get(target).status_code == 200
        assert "/settings" in page.links
        if number > 1:
            assert page.back_links
            assert client.get(page.back_links[0]).status_code == 200
    assert seen == expected
    assert client.get("/settings").status_code == 200
    assert client.get("/testing").status_code == 200


@pytest.mark.parametrize("route", ["/weekly", "/reactive/start", "/reactive/manual", "/reactive/summary"])
def test_mutating_forms_require_the_session_csrf_token(client, route):
    page = Page(client.get(PRINTER + route))
    assert page.fields["csrf_token"]
    data = page.fields.copy()
    data.pop("csrf_token")
    assert client.post(PRINTER + route, data=data).status_code == 403
    data["csrf_token"] = "wrong-session-token"
    assert client.post(PRINTER + route, data=data).status_code == 403
    assert client.get("/api/state").json["records"] == []


def test_weekly_draft_resumes_all_values_and_completes_once(client):
    path = PRINTER + "/weekly"
    data = Page(client.get(path)).fields
    overwrite(data, {"technician_name": "Other", "other_technician": "Visiting technician", "semester": "2",
                 "week_number": "4", "note": "Resume after the nozzle cools", "x_movement_km": "12.5",
                 "nozzle_debris": "on", "save_action": "continue"})
    saved = client.post(path, data=data)
    assert saved.status_code == 302
    assert saved.location == PRINTER + "/weekly/history"
    state = client.get("/api/state").json
    assert len(state["records"]) == 1
    draft = state["records"][0]
    assert draft["state"] == "draft"
    detail = PRINTER + "/weekly/history/" + draft["id"]
    assert detail in Page(client.get(saved.location)).links
    resume = path + "?record=" + draft["id"]
    assert resume in Page(client.get(detail)).links
    fields = Page(client.get(resume)).fields
    assert fields["record_id"] == draft["id"]
    assert fields["technician_name"] == "Other"
    assert fields["other_technician"] == "Visiting technician"
    assert fields["semester"] == "2"
    assert fields["week_number"] == "4"
    assert fields["note"] == "Resume after the nozzle cools"
    assert float(fields["x_movement_km"]) == 12.5
    assert fields["nozzle_debris"] == "on"
    assert "bed_cleaned" not in fields
    fields["save_action"] = "complete"
    assert client.post(resume, data=fields).status_code == 302
    state = client.get("/api/state").json
    assert len(state["records"]) == 1
    completed = state["records"][0]
    assert completed["id"] == draft["id"]
    assert completed["revision"] == draft["revision"] + 1
    assert completed["state"] == "completed"
    assert completed["technician"] == "Visiting technician"
    assert state["printers"][0]["last_weekly_at"] == completed["completed_at"]
    assert client.post(resume, data=fields).status_code == 400
    assert client.get("/api/state").json["records"] == [completed]


def test_invalid_weekly_submission_preserves_entered_data(client):
    path = PRINTER + "/weekly"
    data = Page(client.get(path)).fields
    overwrite(data, {"technician_name": "Technician 2", "note": "Keep this note", "bed_cleaned": "on",
                 "x_movement_km": "not-a-number", "save_action": "complete"})
    response = client.post(path, data=data)
    assert response.status_code == 400
    entered = Page(response, status=400).fields
    assert entered["note"] == "Keep this note"
    assert entered["bed_cleaned"] == "on"
    assert entered["technician_name"] == "Technician 2"
    assert client.get("/api/state").json["records"] == []


@pytest.mark.parametrize("hardware_first", [False, True])
def test_reactive_start_continue_and_resolve_reuse_one_incident(client, hardware_first):
    start = PRINTER + "/reactive/start"
    first_page = Page(client.get(start))
    existing_id = None
    if hardware_first:
        trigger = client.post("/api/trigger", json={"printer_id": "core-one-01"},
                              headers={"X-CSRF-Token": first_page.fields["csrf_token"]})
        assert trigger.status_code == 201
        existing_id = trigger.json["record"]["id"]
    data = Page(client.get(start)).fields
    overwrite(data, {"symptom_category": "filament_not_extruding", "issue_summary": "Filament has stopped moving",
                 "urgency_state": "Under Maintenance", "next_action": "manual"})
    response = client.post(start, data=data)
    assert response.status_code == 302
    assert response.location == PRINTER + "/reactive/manual"
    records = client.get("/api/state").json["records"]
    assert len(records) == 1
    incident = records[0]
    if existing_id:
        assert incident["id"] == existing_id
    data = Page(client.get(response.location)).fields
    assert data["record_id"] == incident["id"]
    assert data["issue_summary"] == "Filament has stopped moving"
    overwrite(data, {"technician_name": "Technician 3", "result_status": "Temporary fix", "action_taken": "Cleaned nozzle",
                 "fix_summary": "Cleaned nozzle; awaiting a test print", "note": "Continue after testing"})
    assert client.post(response.location, data=data).status_code == 302
    interim = client.get("/api/state").json["records"][0]
    assert interim["id"] == incident["id"]
    assert interim["state"] == "in_progress"
    fields = Page(client.get(PRINTER + "/reactive/manual")).fields
    assert fields["note"] == "Continue after testing"
    assert fields.getlist("action_taken") == ["Cleaned nozzle"]
    overwrite(fields, {"result_status": "Fixed and available", "fix_summary": "Test print passed"})
    assert client.post(PRINTER + "/reactive/manual", data=fields).status_code == 302
    state = client.get("/api/state").json
    assert len(state["records"]) == 1
    assert state["records"][0]["state"] == "resolved"
    assert state["records"][0]["fix_summary"] == "Test print passed"
    assert state["printers"][0]["availability"] == "available"
    assert state["printers"][0]["open_incident_id"] is None


def test_diagnosis_navigation_sops_and_accepted_result_are_connected(client):
    category = "filament_not_sticking"
    path = PRINTER + "/diagnosis"
    categories = Page(client.get(path))
    start_link = next(link for link in categories.links if parse_qs(urlsplit(link).query).get("category") == [category])
    question = Page(client.get(start_link))
    answer = next(link for link in question.links if "node=d_filament_not_sticking_dirty_bed" in link)
    result = Page(client.get(answer))
    assert any("node=q_filament_not_sticking_1" in link for link in result.back_links)
    sop_links = [link for link in result.links if link.startswith("/sops/")]
    assert sop_links
    for link in sop_links:
        assert client.get(link).status_code == 200
    accept = next(link for link in result.links if "/reactive/summary?" in link)
    data = Page(client.get(accept)).fields
    overwrite(data, {"technician_name": "Technician 1", "save_action": "available", "fix_summary": "Cleaned and tested the bed"})
    assert client.post(accept, data=data).status_code == 302
    record = client.get("/api/state").json["records"][0]
    assert record["state"] == "resolved"
    assert record["diagnosis_path"] == ["q_filament_not_sticking_1", "d_filament_not_sticking_dirty_bed"]
    assert record["sop_ids"]


def test_unknown_printer_and_cross_printer_record_links_are_rejected(client):
    assert client.get("/printers/missing-printer").status_code == 404
    data = Page(client.get(PRINTER + "/weekly")).fields
    data["save_action"] = "continue"
    assert client.post(PRINTER + "/weekly", data=data).status_code == 302
    record_id = client.get("/api/state").json["records"][0]["id"]
    assert client.get(f"/printers/core-one-02/weekly?record={record_id}").status_code == 404
    assert client.get(f"/printers/core-one-02/weekly/history/{record_id}").status_code == 404
    assert client.get(PRINTER + "/diagnosis?category=missing-category").status_code == 404


def test_bundled_html_sop_images_load_and_cannot_escape_sop_directory(client):
    page = Page(client.get("/sops/Blob%20clean%20instruction%20sheet"))
    sources = [attrs["src"] for tag, attrs in page.elements if tag == "img" and attrs.get("src", "").startswith("/sop-assets/")]
    assert sources
    for source in sources:
        response = client.get(source)
        assert response.status_code == 200
        assert response.content_type.startswith("image/")
    assert client.get("/sop-assets/Blob%20clean%20instruction%20sheet/../../requirements.txt").status_code == 404


def test_manual_continuation_preserves_custom_actions_origin_and_symptom(app, client):
    service = app.extensions["kiosk"]
    saved = service.save_incident({"printer_id": "core-one-01", "summary": "Fan fault",
        "symptom": "Retired symptom label", "source": "diagnosis_assistant", "state": "in_progress",
        "action_taken": "Replaced hotend fan", "fix_summary": "Awaiting test print", "technician": "Technician 1"})
    route = PRINTER + "/reactive/manual"
    fields = Page(client.get(route)).fields
    assert fields.getlist("action_taken") == ["Replaced hotend fan"]
    assert fields["event_origin"] == saved["source"]
    assert fields["symptom_category"] == saved["symptom"]
    fields["result_status"] = "Temporary fix"
    assert client.post(route, data=fields).status_code == 302
    resumed = service.records()[0]
    for key in ("action_taken", "source", "symptom"):
        assert resumed[key] == saved[key]
    fields = Page(client.get(PRINTER + "/reactive/start")).fields
    assert fields["event_origin"] == saved["source"]
    assert fields["symptom_category"] == saved["symptom"]
    fields["next_action"] = "manual"
    assert client.post(PRINTER + "/reactive/start", data=fields).status_code == 302
    assert service.records()[0]["symptom"] == saved["symptom"]
    assert service.records()[0]["source"] == saved["source"]


def test_accepted_fan_diagnosis_keeps_its_symptom_category(client):
    route = PRINTER + "/reactive/summary?diagnosis_node=d_fan_not_spinning_debris&path=q_fan_not_spinning_1"
    fields = Page(client.get(route)).fields
    assert fields["symptom_category"] == "Fan Not Spinning"
    overwrite(fields, {"technician_name": "Technician 1", "fix_summary": "Fan tested", "save_action": "available"})
    assert client.post(route, data=fields).status_code == 302
    assert client.get("/api/state").json["records"][0]["symptom"] == "Fan Not Spinning"


def test_configured_https_and_local_pdf_sops_are_accessible(app, client, tmp_path, monkeypatch):
    from kiosk import ui
    service = app.extensions["kiosk"]
    graph = service.get_diagnosis()
    sop_id = next(iter(graph["sops"]))
    document_url = "https://example.com/maintenance.pdf?a=1&b=2"
    graph["sops"][sop_id]["file"] = document_url
    service.save_diagnosis(graph)
    page = Page(client.get("/sops/" + sop_id + "?printer=core-one-05"))
    assert document_url in page.links
    assert page.back_links == ["/printers/core-one-05/diagnosis"]
    assert "/" in page.links
    monkeypatch.setattr(ui, "SOP_DIR", tmp_path)
    document = tmp_path / "maintenance.pdf"
    document.write_bytes(b"%PDF-1.4\n%%EOF")
    graph["sops"][sop_id]["file"] = "/sops/maintenance.pdf"
    service.save_diagnosis(graph)
    page = Page(client.get("/sops/" + sop_id))
    link = next(link for link in page.links if link.startswith("/sop-documents/"))
    response = client.get(link)
    assert response.status_code == 200
    assert response.content_type == "application/pdf"
    assert response.data == document.read_bytes()
    assert client.get("/sop-documents/../requirements.txt").status_code == 404
    assert client.get("/sop-documents/maintenance.json").status_code == 404


def test_dashboard_readings_ignore_later_edits_to_older_weeks(app, client):
    service = app.extensions["kiosk"]
    old = service.save_weekly({"printer_id": "core-one-01", "week": 1, "metrics": {"filament_m": 1000}})
    service.save_weekly({"printer_id": "core-one-01", "week": 2, "metrics": {"filament_m": 2000}})
    service.save_weekly({"printer_id": "core-one-01", "id": old["id"], "revision": old["revision"],
                         "week": 1, "metrics": {"filament_m": 1500}})
    service.save_incident({"printer_id": "core-one-01", "summary": "Fan fault", "state": "resolved",
                           "technician": "Technician 1", "fix_summary": "Replaced and tested fan"})
    printer = client.get("/api/dashboard").json["printers"][0]
    assert printer["nozzle_life_label"] == "2.00 / 3 km"
    assert printer["recent_fault"] == "Replaced and tested fan"
    service.save_incident({"printer_id": "core-one-01", "summary": "New bed fault"})
    assert client.get("/api/dashboard").json["printers"][0]["recent_fault"] == "New bed fault"


def test_printer_back_and_saved_summary_return_to_its_dashboard_page(client):
    printer = "/printers/core-one-05"
    page = Page(client.get(printer))
    assert page.back_links == ["/dashboard?page=2"]
    fields = Page(client.get(printer + "/reactive/summary?quick_fix=fan-clear")).fields
    overwrite(fields, {"technician_name": "Technician 1", "save_action": "available"})
    response = client.post(printer + "/reactive/summary?quick_fix=fan-clear", data=fields)
    assert response.status_code == 302
    assert response.location == "/dashboard?page=2"
