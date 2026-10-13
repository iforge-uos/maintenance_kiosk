from copy import deepcopy
import json

import pytest

from kiosk.domain import KioskService
from kiosk.diagnostics import run_check
from kiosk import integrations


@pytest.fixture
def service(tmp_path):
    return KioskService(tmp_path / "live")


@pytest.fixture(autouse=True)
def prohibit_accidental_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("A diagnostic test attempted an unmocked network call")
    monkeypatch.setattr(integrations.requests.sessions.Session, "request", blocked)
    monkeypatch.setattr(integrations.smtplib, "SMTP", blocked)
    monkeypatch.setattr(integrations.smtplib, "SMTP_SSL", blocked)


@pytest.mark.parametrize("name", ["config", "offline", "recovery", "trigger"])
def test_local_diagnostics_isolated_from_production(service, name):
    original = deepcopy(service.export_data())
    original.pop("exported_at", None)
    result = run_check(name, service, {"printer_id": "core-one-01", "hold_seconds": 2})
    assert result["status"] == "passed", result
    assert result["duration_ms"] >= 0
    assert result["started_at"]
    after = service.export_data()
    after.pop("exported_at", None)
    assert after == original
    assert service.jobs() == []


def test_recovery_describes_scope_accurately(service):
    result = run_check("recovery", service)
    assert result["details"]["reopened_store"]
    assert result["details"]["process_restart_tested"]
    assert not result["details"]["full_app_restart_tested"]
    assert not result["details"]["external_services_tested"]


def test_missing_sop_documents_are_reported_as_failure(service):
    result = run_check("diagnosis", service)
    assert result["status"] == "failed"
    assert len(result["details"]["missing_documents"]) == 23


def test_diagnosis_checks_cycles_and_unreachable_nodes(service, monkeypatch):
    tree = {"categories": {"cat": {"label": "Category", "start_node": "q"}},
            "nodes": {"q": {"type": "question", "text": "Question", "options": [{"label": "yes", "next": "q"}]},
                      "unused": {"type": "diagnosis", "label": "Fix", "recommended_actions": ["Repair"]}},
            "sops": {}}
    monkeypatch.setattr(service, "get_diagnosis", lambda: tree)
    result = run_check("diagnosis", service)
    assert result["status"] == "failed"
    assert any("Cycle" in error for error in result["details"]["errors"])
    assert result["details"]["unreachable_nodes"] == ["unused"]


@pytest.mark.parametrize("name", ["google", "google_write", "discord"])
def test_missing_connection_reports_unavailable(service, name):
    result = run_check(name, service, {"destination_id": "maintenance"})
    assert result["status"] == "unavailable"


def test_email_requires_explicit_single_recipient(service):
    for recipient in ("", "staff@example.org,other@example.org", "staff@example.org\r\nBcc:evil@example.org"):
        assert run_check("email", service, {"recipient": recipient})["status"] == "failed"


def test_email_test_only_uses_chosen_recipient_and_labels_test(service, monkeypatch):
    settings = service.get_settings()
    settings["email"].update(host="smtp.example.org", sender="kiosk@example.org", recipients=["production@example.org"])
    service.save_settings(settings)
    seen = []

    def send(config, password, payload, test=False):
        seen.append((config, password, payload, test))
        return {"accepted": True, "delivery_confirmed": False, "recipients": config["recipients"]}

    monkeypatch.setattr(integrations, "send_email", send)
    result = run_check("email", service, {"recipient": "operator@example.org"})
    assert result["status"] == "passed"
    assert seen[0][0]["recipients"] == ["operator@example.org"]
    assert seen[0][3] is True
    assert not result["details"]["delivery_confirmed"]
    assert service.records() == service.jobs() == []


def test_discord_test_uses_selected_destination_and_no_queue(service, monkeypatch):
    webhook = "https://discord.com/api/webhooks/123456789/testsecret"
    service.set_secret("discord:maintenance", webhook)
    calls = []

    def send(config, secret, payload, test=False):
        calls.append((config, secret, payload, test))
        return {"accepted": True, "message_id": "123", "channel_id": "456"}

    monkeypatch.setattr(integrations, "send_discord", send)
    result = run_check("discord", service, {"destination_id": "maintenance"})
    assert result["status"] == "passed"
    assert calls[0][1] == webhook
    assert calls[0][3] is True
    assert webhook not in json.dumps(result)
    assert service.jobs() == []


def test_failure_redacts_secret_even_from_integration_exception(service, monkeypatch):
    secret = "https://discord.com/api/webhooks/123456789/private-token"
    service.set_secret("discord:maintenance", secret)

    def send(*args, **kwargs):
        raise integrations.IntegrationError(f"Could not deliver to {secret}")

    monkeypatch.setattr(integrations, "send_discord", send)
    result = run_check("discord", service, {"destination_id": "maintenance"})
    assert result["status"] == "failed"
    assert secret not in json.dumps(result)
    assert "[redacted]" in result["message"]


def test_google_write_reports_cleanup_failure(service, monkeypatch):
    settings = service.get_settings()
    settings["google"]["spreadsheet_id"] = "test-workbook"
    service.save_settings(settings)
    service.set_secret("google_service_account", '{"private_key":"mock-only"}')

    class Google:
        def __init__(self, settings, secrets):
            pass

        def test_write(self):
            return {"verified": True, "cleaned_up": False}

    monkeypatch.setattr(integrations, "GoogleSheets", Google)
    result = run_check("google_write", service)
    assert result["status"] == "failed"
    assert not result["details"]["cleaned_up"]


@pytest.mark.parametrize("owner,folder", [(False, True), (True, False)])
def test_google_connection_does_not_claim_unverified_owner_or_folder(service, monkeypatch, owner, folder):
    settings = service.get_settings()
    settings["google"].update(spreadsheet_id="test-workbook", folder_id="test-folder")
    service.save_settings(settings)
    service.set_secret("google_service_account", '{"private_key":"mock-only"}')

    class Google:
        def __init__(self, settings, secrets):
            pass

        def check(self):
            return {"connected": True, "owner_verified": owner, "folder_verified": folder,
                    "schema_issues": []}

    monkeypatch.setattr(integrations, "GoogleSheets", Google)
    result = run_check("google", service)
    assert result["status"] == "unavailable"
    assert "could not be verified" in result["message"]


def test_unknown_check_is_a_useful_failure(service):
    result = run_check("everything", service)
    assert result["status"] == "failed"
    assert "Unknown" in result["message"]
