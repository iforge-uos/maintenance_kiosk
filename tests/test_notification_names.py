"""Technician names in queued notifications; all external transports are mocked."""
from copy import deepcopy
import ssl
from types import SimpleNamespace

import pytest

from kiosk import integrations
from kiosk.domain import KioskService
from kiosk.worker import SyncWorker


@pytest.fixture
def service(tmp_path, monkeypatch):
    def blocked(*args, **kwargs):
        pytest.fail("A notification name test attempted an external request")

    monkeypatch.setattr(integrations.requests.sessions.Session, "request", blocked)
    monkeypatch.setattr(integrations.smtplib, "SMTP", blocked)
    monkeypatch.setattr(integrations.smtplib, "SMTP_SSL", blocked)
    service = KioskService(tmp_path)
    settings = service.get_settings()
    settings["technicians"][0]["name"] = "Dr Maya Chen"
    settings["technicians"][1]["name"] = "Alex Patel"
    events = ["fault_opened", "incident_resolved", "weekly_completed"]
    settings["email"].update(
        enabled=True, host="smtp.example.org", sender="kiosk@example.org",
        username="kiosk@example.org", recipients=["staff@example.org"], events=events,
    )
    settings["discord"]["enabled"] = True
    settings["discord"]["destinations"][0]["events"] = events
    service.save_settings(settings)
    return service


def incident(service, technician="technician-1"):
    return service.save_incident({
        "printer_id": "core-one-01", "technician": technician, "summary": "Nozzle blocked",
    })


@pytest.mark.parametrize("kind", ["reactive", "weekly"])
def test_worker_sends_names_in_email_and_discord_without_changing_record_identity(service, monkeypatch, kind):
    settings = service.get_settings()
    settings["google"].update(enabled=True, spreadsheet_id="test-workbook-123")
    service.save_settings(settings)
    service.set_secret("smtp_password", "fixture-password")
    service.set_secret("discord:maintenance", "https://discord.com/api/webhooks/123456789012345678/fixture-webhook-token")
    if kind == "weekly":
        record = service.save_weekly({"printer_id": "core-one-01", "technician": "technician-1", "state": "completed"})
    else:
        record = incident(service)
    original_event = service.activity()[0]
    messages = {"email": [], "discord": [], "sheets": []}

    class SMTP:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ehlo(self):
            pass

        def starttls(self, context):
            assert context.verify_mode == ssl.CERT_REQUIRED

        def login(self, username, password):
            assert (username, password) == ("kiosk@example.org", "fixture-password")

        def send_message(self, message, **kwargs):
            messages["email"].append(message.get_content())
            return {}

    def post(url, **kwargs):
        messages["discord"].append(kwargs["json"]["content"])
        assert kwargs["json"]["allowed_mentions"]["parse"] == []
        return SimpleNamespace(status_code=200, headers={}, json=lambda: {"id": "123"})

    class Sheets:
        def __init__(self, *args):
            pass

        def sync(self, event, printers):
            messages["sheets"].append(deepcopy(event))
            return {"synced": True}

    monkeypatch.setattr(integrations.smtplib, "SMTP", lambda *args, **kwargs: SMTP())
    monkeypatch.setattr(integrations.requests, "post", post)
    monkeypatch.setattr("kiosk.worker.GoogleSheets", Sheets)

    report = SyncWorker(service, service.data_dir).run_once()

    assert report["sent"] == 3
    assert report["failed"] == 0
    for channel in ("email", "discord"):
        assert len(messages[channel]) == 1
        assert "Technician: Dr Maya Chen" in messages[channel][0]
        assert "technician-1" not in messages[channel][0]
    assert messages["sheets"] == [original_event]
    assert messages["sheets"][0]["actor"] == "technician-1"
    assert messages["sheets"][0]["record"] == record
    assert service.records() == [record]
    assert record["technician"] == "technician-1"


@pytest.mark.parametrize("change", ["rename", "archive", "remove"])
def test_queued_name_survives_technician_configuration_changes_and_restart(service, change):
    incident(service)
    before = deepcopy(service.jobs())
    settings = service.get_settings()
    if change == "rename":
        settings["technicians"][0]["name"] = "Renamed technician"
    elif change == "archive":
        settings["technicians"][0]["active"] = False
    else:
        settings["technicians"] = settings["technicians"][1:]
    service.save_settings(settings)
    restored = KioskService(service.data_dir)
    worker = SyncWorker(restored, restored.data_dir)

    for job in restored.jobs():
        message = integrations.build_message(worker.notification_payload(job))
        assert "Technician: Dr Maya Chen" in message
        assert "technician-1" not in message
        assert "Renamed technician" not in message
    assert restored.jobs() == before


def test_delayed_notification_uses_event_technician_not_current_record_technician(service):
    record = incident(service)
    queued = deepcopy(service.jobs())
    service.save_incident({
        "id": record["id"], "revision": record["revision"], "printer_id": record["printer_id"],
        "technician": "technician-2", "summary": "Another technician is investigating",
    })
    before = deepcopy(service.records())
    payload = SyncWorker(service, service.data_dir).notification_payload(queued[0])

    assert payload["current_state"]["technician"] == "technician-2"
    message = integrations.build_message(payload)
    assert "Technician: Dr Maya Chen" in message
    assert "Alex Patel" not in message
    assert service.records() == before
    assert service.jobs() == queued


def test_legacy_queued_id_resolves_archived_name_without_rewriting_payload(service):
    incident(service)
    event = service.activity()[0]
    event.pop("technician_name", None)
    event["change_id"] = "legacy-event"
    settings = service.get_settings()
    settings["technicians"][0]["active"] = False
    service.save_settings(settings)
    job = service.queue_job("email", settings["email"], event)
    before = deepcopy(service.jobs())
    payload = SyncWorker(service, service.data_dir).notification_payload(job)

    assert "Technician: Dr Maya Chen" in integrations.build_message(payload)
    assert payload["record"]["technician"] == "technician-1"
    assert "technician_name" not in job["payload"]
    assert service.jobs() == before


@pytest.mark.parametrize("technician", ["Visitor Lee", "unknown-technician-id", ""])
def test_free_text_and_unassigned_technicians_keep_their_existing_meaning(service, technician):
    record = incident(service, technician)
    payload = SyncWorker(service, service.data_dir).notification_payload(service.jobs()[0])
    message = integrations.build_message(payload)

    if technician:
        assert f"Technician: {technician}" in message
    else:
        assert "Technician:" not in message
    assert payload["record"]["technician"] == technician
    assert service.records() == [record]
