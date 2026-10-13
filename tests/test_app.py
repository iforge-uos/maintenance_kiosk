import json

import pytest

from kiosk import create_app


@pytest.fixture
def app(tmp_path):
    return create_app({"TESTING": True, "DATA_DIR": tmp_path})


@pytest.fixture
def client(app):
    client = app.test_client()
    with client.session_transaction() as session:
        session["csrf"] = "test-csrf"
    return client


HEADERS = {"X-CSRF-Token": "test-csrf"}


def test_health_and_csrf_protection(client):
    assert client.get("/api/health").json["storage"] == "local-files"
    rejected = client.post("/api/trigger", json={"printer_id": "core-one-01"})
    assert rejected.status_code == 403
    assert client.get("/api/state").json["records"] == []


def test_incident_lifecycle_persists_across_app_restart(client, app):
    first = client.post("/api/trigger", json={"printer_id": "core-one-01"}, headers=HEADERS)
    assert first.status_code == 201, first.json
    incident = first.json["record"]
    repeated = client.post("/api/trigger", json={"printer_id": "core-one-01"}, headers=HEADERS)
    assert repeated.json["record"]["id"] == incident["id"]
    resolved = client.post("/api/incidents", json={
        "id": incident["id"], "revision": incident["revision"], "printer_id": "core-one-01",
        "technician": "Technician", "state": "resolved", "availability": "available",
        "fix_summary": "Cleared nozzle", "action_taken": "Cold pull",
    }, headers=HEADERS)
    assert resolved.status_code == 201, resolved.json
    second_app = create_app({"TESTING": True, "DATA_DIR": app.config["DATA_DIR"]})
    restored = second_app.test_client().get("/api/state").json
    assert len(restored["records"]) == 1
    assert restored["records"][0]["state"] == "resolved"
    assert restored["printers"][0]["availability"] == "available"


def test_weekly_validation_and_completion(client):
    settings = client.get("/api/state").json["settings"]
    settings["checklist"][0]["required"] = True
    assert client.put("/api/settings", json=settings, headers=HEADERS).status_code == 200
    payload = {
        "printer_id": "core-one-01", "technician": "Technician", "academic_year": "2026/27",
        "semester": 1, "week": 1, "state": "completed", "checklist": {}, "metrics": {},
    }
    assert client.post("/api/weekly", json=payload, headers=HEADERS).status_code == 400
    payload["checklist"] = {item["id"]: True for item in settings["checklist"]}
    payload["metrics"] = {item["id"]: 12 for item in settings["metrics"]}
    saved = client.post("/api/weekly", json=payload, headers=HEADERS)
    assert saved.status_code == 201, saved.json
    assert saved.json["record"]["completed_at"]
    assert client.get("/api/state").json["printers"][0]["last_weekly_at"]


def test_secrets_never_leave_settings_state_or_exports(client):
    saved = client.put("/api/secrets/smtp_password", json={"value": "do-not-expose-this-test-password"}, headers=HEADERS)
    assert saved.status_code == 200
    assert saved.json["secret_status"]["smtp_password"] is True
    for url in ["/api/state", "/api/settings", "/api/export", "/api/settings/export", "/api/settings/history"]:
        response = client.get(url)
        assert response.status_code == 200
        assert b"do-not-expose-this-test-password" not in response.data
        assert response.headers["Cache-Control"] == "no-store"


def test_unknown_secret_and_invalid_google_credentials_rejected(client):
    assert client.put("/api/secrets/unknown", json={"value": "x"}, headers=HEADERS).status_code == 400
    assert client.put("/api/secrets/google_service_account", json={"value": "invalid"}, headers=HEADERS).status_code == 400
    assert client.put("/api/secrets/google_service_account", json={"value": "[]"}, headers=HEADERS).status_code == 400
    assert client.get("/auth/google/callback?state=untrusted&code=x").status_code == 400


def test_settings_change_requires_fresh_revision(client):
    original = client.get("/api/settings").json["settings"]
    changed = json.loads(json.dumps(original))
    changed["general"]["farm_name"] = "Workshop"
    saved = client.put("/api/settings", json=changed, headers=HEADERS)
    assert saved.status_code == 200, saved.json
    assert client.put("/api/settings", json=original, headers=HEADERS).status_code == 400
    history = client.get("/api/settings/history").json["versions"]
    assert len(history) >= 2
