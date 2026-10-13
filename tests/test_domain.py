from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os

import pytest

from kiosk.domain import KioskService, validate_diagnosis
from kiosk import storage


@pytest.fixture
def service(tmp_path):
    return KioskService(tmp_path / "data")


def enable_routes(service):
    settings = service.get_settings()
    settings["google"].update(enabled=True, spreadsheet_id="test-workbook")
    settings["email"].update(enabled=True, host="smtp.example.org", sender="kiosk@example.org", recipients=["staff@example.org"])
    settings["discord"]["enabled"] = True
    service.save_settings(settings)


def weekly_payload(service, **updates):
    payload = {
        "printer_id": "core-one-01", "technician": "Alex", "academic_year": "2026/27", "semester": 1, "week": 1,
        "checklist": {item["id"]: True for item in service.get_settings()["checklist"]},
        "metrics": {"x_movement_km": 2.5, "z_movement_m": 10, "total_print_hours": 200},
        "note": "Checked screws and cleaned bed", "state": "completed",
    }
    return {**payload, **updates}


def test_trigger_deduplicates_record_and_notifications_across_restart(service):
    enable_routes(service)
    first = service.trigger("core-one-01")
    restarted = KioskService(service.data_dir)
    assert restarted.trigger("core-one-01") == first
    assert len(restarted.records()) == 1
    assert len(restarted.activity()) == 1
    assert {job["kind"] for job in restarted.jobs()} == {"sheets", "email", "discord"}
    assert len(restarted.jobs()) == 3
    assert restarted.printer_summaries()[0]["availability"] == "unavailable"


def test_parallel_trigger_uses_one_durable_transaction(service):
    enable_routes(service)
    def trigger(_):
        return KioskService(service.data_dir).trigger("core-one-01")["id"]
    with ThreadPoolExecutor(max_workers=8) as executor:
        ids = list(executor.map(trigger, range(24)))
    assert len(set(ids)) == 1
    assert len(service.activity()) == 1
    assert len(service.jobs()) == 3


def test_interrupted_write_does_not_accept_half_a_record(service, monkeypatch):
    enable_routes(service)
    def fail_before_rename(*args):
        raise OSError("simulated interrupted disk write")
    monkeypatch.setattr(storage.os, "replace", fail_before_rename)
    with pytest.raises(OSError):
        service.trigger("core-one-01")
    assert service.records() == []
    assert service.jobs() == []


def test_restart_ignores_uncommitted_temp_and_rebuilds_jobs(service):
    enable_routes(service)
    record = service.trigger("core-one-01")
    pending = service.pending_jobs()
    (service.data_dir / "journal" / ".pending-crash").write_text('{"record":')
    reopened = KioskService(service.data_dir)
    assert reopened.records()[0] == record
    assert reopened.pending_jobs() == pending


def test_ambiguous_commit_replayed_without_duplicate_trigger(service, monkeypatch):
    enable_routes(service)
    original = storage.atomic_json
    def commit_then_crash(path, value):
        original(path, value)
        raise OSError("process died after rename")
    monkeypatch.setattr(storage, "atomic_json", commit_then_crash)
    with pytest.raises(OSError):
        service.trigger("core-one-01")
    monkeypatch.setattr(storage, "atomic_json", original)
    reopened = KioskService(service.data_dir)
    assert reopened.trigger("core-one-01")["id"] == reopened.records()[0]["id"]
    assert len(reopened.activity()) == 1
    assert len(reopened.jobs()) == 3


def test_corrupt_committed_journal_fails_closed(service):
    service.trigger("core-one-01")
    next((service.data_dir / "journal").glob("*.json")).write_text("not JSON")
    with pytest.raises(RuntimeError, match="unreadable"):
        service.records()


def test_weekly_resume_completion_and_natural_key_dedup(service):
    draft = service.save_weekly(weekly_payload(service, state="draft", checklist={}))
    saved = service.save_weekly(weekly_payload(service))
    assert saved["id"] == draft["id"]
    assert saved["revision"] == 2
    assert saved["completed_at"]
    assert service.save_weekly(weekly_payload(service)) == saved
    assert len(service.records("weekly")) == 1
    assert [event["event_type"] for event in service.activity()] == ["weekly_saved", "weekly_completed"]


def test_weekly_completion_requires_checklist_and_operator(service):
    settings = service.get_settings()
    settings["checklist"][0]["required"] = True
    service.save_settings(settings)
    with pytest.raises(ValueError, match="Complete required checklist"):
        service.save_weekly(weekly_payload(service, checklist={}))
    with pytest.raises(ValueError, match="Technician"):
        service.save_weekly(weekly_payload(service, technician=""))
    for invalid in (-1, float("nan"), float("inf"), True, "4"):
        with pytest.raises(ValueError, match="Cumulative usage"):
            service.save_weekly(weekly_payload(service, metrics={"total_print_hours": invalid}))
    assert service.records() == []


def test_weekly_snapshots_historical_checklist_meaning(service):
    first = service.save_weekly(weekly_payload(service))
    settings = service.get_settings()
    settings["checklist"][0]["label"] = "Changed checklist meaning"
    settings["metrics"][0]["unit"] = "m"
    service.save_settings(settings)
    corrected = service.save_weekly(weekly_payload(service, id=first["id"], revision=first["revision"], note="Corrected notes"))
    assert corrected["checklist_definitions"] == first["checklist_definitions"]
    assert corrected["metric_definitions"] == first["metric_definitions"]
    assert service.activity()[0]["record"]["note"] == "Checked screws and cleaned bed"


def test_stale_records_cannot_overwrite_a_saved_revision(service):
    first = service.trigger("core-one-01")
    service.save_incident({"id": first["id"], "revision": 1, "printer_id": "core-one-01", "state": "in_progress"})
    with pytest.raises(ValueError, match="another session"):
        service.save_incident({"id": first["id"], "revision": 1, "printer_id": "core-one-01", "note": "stale edit"})


def test_incident_resolution_changes_printer_and_routes_once(service):
    enable_routes(service)
    opened = service.trigger("core-one-01")
    resolved = service.save_incident({"id": opened["id"], "printer_id": "core-one-01", "state": "resolved", "technician": "Alex", "fix_summary": "Replaced nozzle"})
    assert resolved["resolved_at"]
    assert service.printer_summaries()[0]["availability"] == "available"
    assert service.printer_summaries()[0]["open_incident_id"] is None
    assert len(service.jobs()) == 6
    assert service.save_incident({"id": opened["id"], "printer_id": "core-one-01", "state": "resolved", "technician": "Alex", "fix_summary": "Replaced nozzle"}) == resolved
    assert len(service.jobs()) == 6
    assert service.trigger("core-one-01")["id"] != opened["id"]


def test_settings_versions_validation_and_historical_printer_archive(service):
    first = service.get_settings()
    changed = deepcopy(first)
    changed["general"]["farm_name"] = "New name"
    second = service.save_settings(changed)
    assert second["revision"] == 2
    assert [item["revision"] for item in service.settings_history()] == [1, 2]
    with pytest.raises(ValueError, match="another session"):
        service.save_settings(first)
    service.trigger("core-one-01")
    current = service.get_settings()
    current["printers"] = current["printers"][1:]
    current["hardware"]["mappings"] = []
    with pytest.raises(ValueError, match="archived"):
        service.save_settings(current)
    current = service.get_settings()
    current["printers"][0]["active"] = False
    service.save_settings(current)
    assert service.records()[0]["printer_name"] == "Bell"


@pytest.mark.parametrize("section,key,value", [
    ("general", "timezone", "Not/A_Zone"),
    ("general", "refresh_seconds", 0),
    ("hardware", "hold_seconds", float("nan")),
    ("email", "security", "none"),
    ("google", "spreadsheet_id", "https://docs.google.com/spreadsheets/d/x"),
])
def test_invalid_settings_never_activate(service, section, key, value):
    settings = service.get_settings()
    settings[section][key] = value
    with pytest.raises(ValueError):
        service.save_settings(settings)
    assert service.get_settings()["revision"] == 1


def test_duplicate_gpio_pin_rejected(service):
    settings = service.get_settings()
    settings["hardware"]["mappings"][0]["led_pin"] = 17
    with pytest.raises(ValueError, match="only one"):
        service.save_settings(settings)


def test_pending_destinations_preserved_and_migration_blocked(service):
    enable_routes(service)
    service.trigger("core-one-01")
    queued = deepcopy(service.jobs())
    settings = service.get_settings()
    settings["google"]["spreadsheet_id"] = "different-book"
    with pytest.raises(ValueError, match="outstanding Sheets"):
        service.save_settings(settings)
    settings = service.get_settings()
    settings["email"]["recipients"] = ["different@example.org"]
    with pytest.raises(ValueError, match="outstanding email"):
        service.save_settings(settings)
    settings = service.get_settings()
    settings["discord"]["destinations"] = []
    with pytest.raises(ValueError, match="outstanding Discord"):
        service.save_settings(settings)
    assert service.jobs() == queued
    for job in queued:
        service.update_job(job["id"], status="sent", attempts=1, result={"accepted": True})
    settings = service.get_settings()
    settings["google"]["spreadsheet_id"] = "different-book"
    assert service.save_settings(settings)["google"]["spreadsheet_id"] == "different-book"


def test_job_retries_are_durable_and_explicit_queue_deduplicates(service):
    service.trigger("core-one-01")
    event = service.activity()[0]
    destination = service.get_settings()["google"]
    job = service.queue_job("sheets", destination, event)
    assert service.queue_job("sheets", destination, event) == job
    retry = service.update_job(job["id"], status="failed", attempts=1, last_error="Temporary outage", next_attempt_at="2026-10-12T22:00:00+00:00", retryable=True)
    reopened = KioskService(service.data_dir)
    assert reopened.pending_jobs() == [retry]
    reopened.update_job(job["id"], status="sent", attempts=2, result={"change_id": event["change_id"]})
    assert service.pending_jobs() == []


def test_secrets_stay_in_vault_outside_settings_journal_and_exports(service):
    secret = "unit-test-only-secret-value"
    service.set_secret("smtp_password", secret)
    service.set_secret("google_oauth_token", json.dumps({"refresh_token": secret}))
    assert service.get_secret("smtp_password") == secret
    assert service.secret_status()["smtp_password"] is True
    assert (service.data_dir / "secrets.json").stat().st_mode & 0o777 == 0o600
    enable_routes(service)
    service.trigger("core-one-01")
    for data in (service.export_data(), service.get_settings(), service.jobs(), service.secret_status()):
        assert secret not in json.dumps(data)
    for path in (service.data_dir / "journal").glob("*.json"):
        assert secret not in path.read_text()
    settings = service.get_settings()
    settings["email"]["password"] = secret
    with pytest.raises(ValueError, match="separate connection"):
        service.save_settings(settings)


def test_missing_credentials_can_be_added_for_queued_work(service):
    enable_routes(service)
    service.trigger("core-one-01")
    service.set_secret("discord:maintenance", "https://discord.com/api/webhooks/123/first-token")
    with pytest.raises(ValueError, match="outstanding jobs"):
        service.set_secret("discord:maintenance", "https://discord.com/api/webhooks/456/different-destination")
    with pytest.raises(ValueError, match="outstanding jobs"):
        service.set_secret("discord:maintenance", "")


def test_failed_delivery_credentials_can_rotate_without_rerouting(service):
    service.set_secret("smtp_password", "expired-password")
    service.set_secret("discord:maintenance", "https://discord.com/api/webhooks/123/expired-token")
    service.set_secret("google_service_account", json.dumps({"client_email": "kiosk@example.iam.gserviceaccount.com", "private_key": "old"}))
    service.set_secret("google_oauth_token", json.dumps({"client_id": "same-client", "refresh_token": "old"}))
    enable_routes(service)
    service.trigger("core-one-01")
    queued = service.jobs()
    service.set_secret("smtp_password", "new-password")
    service.set_secret("discord:maintenance", "https://discord.com/api/webhooks/123/new-token")
    service.set_secret("google_service_account", json.dumps({"client_email": "kiosk@example.iam.gserviceaccount.com", "private_key": "new"}))
    service.set_secret("google_oauth_token", json.dumps({"client_id": "same-client", "refresh_token": "new"}))
    assert service.get_secret("smtp_password") == "new-password"
    assert service.jobs() == queued
    with pytest.raises(ValueError, match="outstanding jobs"):
        service.set_secret("google_service_account", json.dumps({"client_email": "different@example.iam.gserviceaccount.com", "private_key": "new"}))
    with pytest.raises(ValueError, match="outstanding jobs"):
        service.set_secret("google_oauth_token", json.dumps({"client_id": "other-client", "refresh_token": "new"}))


def test_diagnosis_rejects_cycles_broken_edges_and_unreachable_nodes(service):
    valid = service.get_diagnosis()
    validate_diagnosis(valid)
    first = next(key for key, node in valid["nodes"].items() if node["type"] == "question")
    broken = deepcopy(valid)
    broken["nodes"][first]["options"][0]["next"] = "missing-node"
    with pytest.raises(ValueError, match="broken"):
        service.save_diagnosis(broken)
    cyclic = deepcopy(valid)
    cyclic["nodes"][first]["options"][0]["next"] = first
    with pytest.raises(ValueError, match="cycle"):
        service.save_diagnosis(cyclic)
    unreachable = deepcopy(valid)
    unreachable["nodes"]["unused"] = {"type": "diagnosis", "label": "Unreachable", "sop_ids": []}
    with pytest.raises(ValueError, match="reachable"):
        service.save_diagnosis(unreachable)
    valid["categories"][next(iter(valid["categories"]))]["label"] = "Updated category"
    service.save_diagnosis(valid)
    assert KioskService(service.data_dir).get_diagnosis() == valid


@pytest.mark.parametrize("method,field", [
    ("save_weekly", "id"), ("save_weekly", "printer_id"), ("save_weekly", "state"),
    ("save_incident", "id"), ("save_incident", "printer_id"), ("save_incident", "state"),
    ("save_incident", "availability"),
])
@pytest.mark.parametrize("malformed", [[], {}, ["unexpected"], {"unexpected": "value"}, True])
def test_malformed_record_identifiers_and_enums_are_validation_errors(service, method, field, malformed):
    existing = service.trigger("core-one-02")
    before = service.activity()
    payload = {"printer_id": "core-one-01", field: malformed}
    with pytest.raises(ValueError):
        getattr(service, method)(payload)
    assert service.activity() == before
    assert service.records() == [existing]
    assert service.jobs() == []


@pytest.mark.parametrize("path,malformed", [
    (("google", "auth_mode"), []),
    (("email", "security"), {}),
    (("hardware", "backend"), []),
    (("hardware", "mappings", 0, "printer_id"), {}),
    (("hardware", "mappings", 0, "button_pin"), True),
    (("hardware", "mappings", 0, "led_pin"), [27]),
    (("hardware", "mappings"), [[]]),
    (("discord", "destinations", 0, "printer_ids"), [["core-one-01"]]),
    (("discord", "destinations", 0, "events"), [{}]),
    (("discord", "destinations", 0, "id"), {}),
    (("email", "recipients"), [[]]),
    (("printers", 0, "id"), []),
    (("technicians",), {}),
    (("general", "refresh_seconds"), 10**400),
    (("revision",), True),
])
def test_malformed_nested_settings_never_activate(service, path, malformed):
    original = service.get_settings()
    settings = deepcopy(original)
    target = settings
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = malformed
    with pytest.raises(ValueError):
        service.save_settings(settings)
    assert service.get_settings() == original
    assert service.settings_history() == [original]


@pytest.mark.parametrize("method,overrides", [
    ("save_incident", {"diagnosis_path": [["node"]]}),
    ("save_incident", {"sop_ids": [{}]}),
    ("save_incident", {"revision": True}),
    ("save_incident", {"revision": {"revision": 1}}),
    ("save_weekly", {"checklist": []}),
    ("save_weekly", {"checklist": {"bed_cleaned": []}}),
    ("save_weekly", {"metrics": {"total_print_hours": True}}),
    ("save_weekly", {"metrics": {"total_print_hours": 10**400}}),
    ("save_weekly", {"revision": []}),
    ("save_weekly", {"revision": 10**400}),
    ("save_weekly", {"semester": True}),
])
def test_malformed_record_nested_values_leave_no_journal_entry(service, method, overrides):
    with pytest.raises(ValueError):
        getattr(service, method)({"printer_id": "core-one-01", **overrides})
    assert service.activity() == []
    assert service.records() == []
    assert not list((service.data_dir / "journal").glob("*.json"))


def test_malformed_delivery_updates_do_not_damage_pending_jobs(service):
    enable_routes(service)
    service.trigger("core-one-01")
    job = service.pending_jobs()[0]
    before = service.jobs()
    for invalid in ({"status": []}, {"retryable": []}, {"attempts": True}, {"result": []}, {"last_attempt_at": {}}):
        with pytest.raises(ValueError):
            service.update_job(job["id"], **invalid)
        assert service.jobs() == before
    for invalid_id in ([], {}, True):
        with pytest.raises(ValueError):
            service.update_job(invalid_id, status="sent")
    assert service.jobs() == before
