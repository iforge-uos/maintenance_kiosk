"""Offline adapter tests: no Google, SMTP, or Discord requests leave this process."""
from copy import deepcopy
import json
import smtplib
import ssl
from urllib.parse import unquote, urlsplit

import pytest
import requests

from kiosk import integrations as integration
from kiosk.integrations import GoogleSheets, HEADERS, IntegrationError, TEST_HEADERS, TEST_TAB


class Response:
    def __init__(self, status=200, data=None, headers=None):
        self.status_code = status
        self.data = {} if data is None else data
        self.headers = headers or {}

    def json(self):
        return deepcopy(self.data)


class FakeGoogle:
    def __init__(self):
        self.tabs = {}
        self.calls = []
        self.owner = "threedprintingiforge@gmail.com"
        self.parents = []
        self.fail_after_append = None
        self.fail_projection_once = False
        self.drive_forbidden = False
        self.fail_cleanup = False
        self.column_counts = {}

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, deepcopy(kwargs)))
        assert kwargs["timeout"] == integration.TIMEOUT
        assert kwargs["allow_redirects"] is False
        if url.startswith(integration.DRIVE_ROOT):
            if self.drive_forbidden:
                return Response(403, {"error": {"message": "secret from backend"}})
            if url.endswith("folder-12345678"):
                return Response(data={"mimeType": "application/vnd.google-apps.folder",
                                      "owners": [{"emailAddress": self.owner}]})
            return Response(data={"mimeType": "application/vnd.google-apps.spreadsheet",
                                  "owners": [{"emailAddress": self.owner}] if self.owner else [], "parents": self.parents,
                                  "capabilities": {"canEdit": True}})
        if url.endswith(":batchUpdate"):
            for item in kwargs["json"]["requests"]:
                if "addSheet" in item:
                    self.tabs[item["addSheet"]["properties"]["title"]] = []
                elif "updateSheetProperties" in item:
                    props = item["updateSheetProperties"]["properties"]
                    tab = list(self.tabs)[props["sheetId"]]
                    self.column_counts[tab] = props["gridProperties"]["columnCount"]
                else:
                    assert "setDataValidation" in item or "repeatCell" in item
            return Response()
        if url.endswith("/values:batchClear"):
            if self.fail_cleanup:
                return Response(503)
            for target in kwargs["json"]["ranges"]:
                tab, cell = target.split("!")
                row = int(cell.split(":")[0])
                self.tabs[tab.strip("'")][row - 1] = []
            return Response()
        if "/values/" not in url:
            return Response(data={"properties": {"title": "Maintenance Records"}, "sheets": [
                {"properties": {"sheetId": index, "title": tab,
                                "gridProperties": {"columnCount": self.column_counts.get(tab, 26)}}}
                for index, tab in enumerate(self.tabs)]})
        target = unquote(url.split("/values/")[1])
        appending = target.endswith(":append")
        if appending:
            target = target[:-7]
        tab, _, cell = target.partition("!")
        tab = tab.strip("'")
        if method == "GET":
            values = self.tabs[tab]
            if cell == "1:1":
                values = values[:1]
            return Response(data={"values": values})
        assert kwargs["params"]["valueInputOption"] == "RAW"
        if self.fail_projection_once and tab == "Weekly_Maintenance" and self.tabs[tab]:
            self.fail_projection_once = False
            return Response(503)
        if appending:
            self.tabs[tab].extend(deepcopy(kwargs["json"]["values"]))
            if self.fail_after_append == tab:
                self.fail_after_append = None
                raise requests.Timeout("Sensitive URL? token=never-show")
            return Response(data={"updates": {"updatedRows": 1}})
        row = int(cell[1:])
        while len(self.tabs[tab]) < row:
            self.tabs[tab].append([])
        self.tabs[tab][row - 1] = deepcopy(kwargs["json"]["values"][0])
        return Response()


@pytest.fixture
def sheets():
    adapter = object.__new__(GoogleSheets)
    adapter.settings = {"google": {"account_email": "threedprintingiforge@gmail.com"}}
    adapter.config = adapter.settings["google"]
    adapter.spreadsheet_id = "spreadsheet-12345678"
    adapter.base_url = integration.SHEETS_ROOT + "/" + adapter.spreadsheet_id
    adapter.auth_mode = "service_account"
    adapter.credential_email = "kiosk@example.iam.gserviceaccount.com"
    adapter._drive_checked = False
    adapter.session = FakeGoogle()
    return adapter


@pytest.fixture
def event():
    return {"change_id": "change-1", "event_type": "weekly_completed", "actor": "Sam",
            "occurred_at": "2026-10-12T12:00:00+00:00", "record": {
                "kind": "weekly", "id": "weekly-1", "revision": 1, "printer_id": "core-1",
                "printer_name": "Core 1", "technician": "Sam", "academic_year": "2026/27",
                "semester": 1, "week": 1, "state": "completed", "checklist": {"bed": True},
                "checklist_definitions": [{"id": "bed", "label": "Bed cleaned"}],
                "metrics": {"hours": 20}, "metric_definitions": [{"id": "hours", "unit": "hours"}],
                "settings_revision": 2, "note": "=IMPORTDATA(\"https://invalid.test\")",
                "created_at": "2026-10-12T12:00:00+00:00", "updated_at": "2026-10-12T12:00:00+00:00"}}


def data_rows(sheets, tab):
    return [row for row in sheets.session.tabs[tab][1:] if row]


def test_read_only_check_reports_missing_tabs_and_account(sheets):
    result = sheets.check()
    assert result["owner_verified"] is True
    assert result["missing_tabs"] == list(HEADERS)
    assert result["account_email"] == "threedprintingiforge@gmail.com"
    assert all(method == "GET" for method, _, _ in sheets.session.calls)


def test_wrong_drive_owner_is_rejected_before_any_writes(sheets, event):
    sheets.session.owner = "someone-else@example.com"
    with pytest.raises(IntegrationError, match="owner does not match"):
        sheets.sync(event, [])
    assert all(method == "GET" for method, _, _ in sheets.session.calls)


def test_selected_folder_membership_is_checked(sheets):
    sheets.config["folder_id"] = "folder-12345678"
    with pytest.raises(IntegrationError, match="not in the selected"):
        sheets.check()
    sheets.session.parents = ["folder-12345678"]
    assert sheets.check()["folder_verified"] is True


def test_missing_drive_metadata_is_reported_honestly(sheets):
    sheets.session.drive_forbidden = True
    result = sheets.check()
    assert result["owner_verified"] is False
    assert "could not be verified" in result["warnings"][0]


@pytest.mark.parametrize("operation", ["sync", "test_write"])
@pytest.mark.parametrize("unverified", ["forbidden", "missing_owner"])
def test_google_never_writes_when_ownership_is_unverified(sheets, event, operation, unverified):
    if unverified == "forbidden":
        sheets.session.drive_forbidden = True
    else:
        sheets.session.owner = None
    with pytest.raises(IntegrationError, match="ownership could not be verified"):
        if operation == "sync":
            sheets.sync(event, [])
        else:
            sheets.test_write()
    assert all(method == "GET" for method, _, _ in sheets.session.calls)


def test_sync_is_idempotent_preserves_complete_audit_and_raw_formulas(sheets, event):
    printers = [{"id": "core-1", "name": "Core 1", "active": True, "availability": "available"}]
    sheets.sync(event, printers)
    result = sheets.sync(event, printers)
    assert result["audit_already_present"] is True
    assert len(data_rows(sheets, "Activity_Log")) == 1
    assert len(data_rows(sheets, "Weekly_Maintenance")) == 1
    assert len(data_rows(sheets, "Printers")) == 1
    assert json.loads(data_rows(sheets, "Activity_Log")[0][7]) == event["record"]
    assert data_rows(sheets, "Weekly_Maintenance")[0][HEADERS["Weekly_Maintenance"].index("note")] == event["record"]["note"]
    for method, url, kwargs in sheets.session.calls:
        if method in {"PUT", "POST"} and "/values/" in url:
            assert kwargs["params"]["valueInputOption"] == "RAW"


def test_ambiguous_audit_append_recovers_without_duplicate(sheets, event):
    sheets.session.fail_after_append = "Activity_Log"
    with pytest.raises(IntegrationError) as caught:
        sheets.sync(event, [])
    assert caught.value.retryable is True
    assert "never-show" not in str(caught.value)
    assert len(data_rows(sheets, "Activity_Log")) == 1
    assert data_rows(sheets, "Weekly_Maintenance") == []
    sheets.sync(event, [])
    assert len(data_rows(sheets, "Activity_Log")) == 1
    assert len(data_rows(sheets, "Weekly_Maintenance")) == 1


def test_audit_success_projection_failure_is_repaired(sheets, event):
    sheets.session.fail_projection_once = True
    with pytest.raises(IntegrationError):
        sheets.sync(event, [])
    sheets.sync(event, [])
    assert len(data_rows(sheets, "Activity_Log")) == 1
    assert len(data_rows(sheets, "Weekly_Maintenance")) == 1


def test_old_retry_does_not_overwrite_newer_record(sheets, event):
    sheets.sync(event, [])
    newer = deepcopy(event)
    newer["change_id"] = "change-2"
    newer["record"].update(revision=2, note="Corrected reading")
    sheets.sync(newer, [])
    result = sheets.sync(event, [])
    assert result["projection"] == "newer_revision_preserved"
    assert data_rows(sheets, "Weekly_Maintenance")[0][1] == 2
    assert len(data_rows(sheets, "Activity_Log")) == 2


def test_weekly_columns_are_typed_readable_and_include_per_record_units(sheets, event):
    event["record"]["checklist"]["bed"] = False
    event["record"]["metrics"]["hours"] = 0
    sheets.sync(event, [])
    headers = sheets.session.tabs["Weekly_Maintenance"][0]
    row = dict(zip(headers, data_rows(sheets, "Weekly_Maintenance")[0]))
    assert row["checklist:bed"] is False
    assert row["metric:hours"] == 0
    assert isinstance(row["metric:hours"], int)
    assert row["unit:hours"] == "hours"
    requests_body = [item for method, url, kwargs in sheets.session.calls
                     if method == "POST" and url.endswith(":batchUpdate")
                     for item in kwargs["json"]["requests"]]
    checkbox_rules = [item["setDataValidation"] for item in requests_body if "setDataValidation" in item]
    assert checkbox_rules[0]["rule"]["condition"]["type"] == "BOOLEAN"
    assert checkbox_rules[0]["range"]["startColumnIndex"] == headers.index("checklist:bed")
    numeric_rules = [item["repeatCell"] for item in requests_body if "repeatCell" in item]
    assert numeric_rules[0]["range"]["startColumnIndex"] == headers.index("metric:hours")


def test_weekly_schema_extensions_preserve_old_columns_and_historical_units(sheets, event):
    sheets.sync(event, [])
    original_headers = sheets.session.tabs["Weekly_Maintenance"][0][:]
    original_row = data_rows(sheets, "Weekly_Maintenance")[0][:]
    next_week = deepcopy(event)
    next_week["change_id"] = "change-next-week"
    record = next_week["record"]
    record.update(id="weekly-2", week=2, checklist={"nozzle": True},
                  checklist_definitions=[{"id": "nozzle", "label": "New nozzle check"}],
                  metrics={"hours": 120, "filament": 25.5},
                  metric_definitions=[{"id": "hours", "unit": "minutes"}, {"id": "filament", "unit": "m"}])
    sheets.sync(next_week, [])
    headers = sheets.session.tabs["Weekly_Maintenance"][0]
    assert headers[:len(original_headers)] == original_headers
    assert headers[len(original_headers):] == ["checklist:nozzle", "metric:filament", "unit:filament"]
    assert data_rows(sheets, "Weekly_Maintenance")[0] == original_row
    old_values = dict(zip(headers, original_row))
    new_values = dict(zip(headers, data_rows(sheets, "Weekly_Maintenance")[1]))
    assert old_values["unit:hours"] == "hours"
    assert new_values["unit:hours"] == "minutes"
    assert new_values["checklist:bed"] == ""
    assert new_values["checklist:nozzle"] is True
    assert new_values["metric:filament"] == 25.5
    sheets.sync(event, [])
    assert sheets.session.tabs["Weekly_Maintenance"][0] == headers
    assert sheets.check()["schema_issues"] == []


def test_old_revision_retry_preserves_newer_named_column_values(sheets, event):
    sheets.sync(event, [])
    newer = deepcopy(event)
    newer["change_id"] = "change-2"
    newer["record"]["revision"] = 2
    newer["record"]["metrics"]["hours"] = 200
    newer["record"]["metric_definitions"][0]["unit"] = "minutes"
    sheets.sync(newer, [])
    sheets.sync(event, [])
    headers = sheets.session.tabs["Weekly_Maintenance"][0]
    row = dict(zip(headers, data_rows(sheets, "Weekly_Maintenance")[0]))
    assert row["metric:hours"] == 200
    assert row["unit:hours"] == "minutes"


def test_weekly_column_grid_expands_beyond_az_without_losing_fields(sheets, event):
    record = event["record"]
    record["checklist_definitions"] = [{"id": f"check-{index}"} for index in range(40)]
    record["checklist"] = {f"check-{index}": True for index in range(40)}
    sheets.sync(event, [])
    headers = sheets.session.tabs["Weekly_Maintenance"][0]
    assert len(headers) > 52
    assert sheets.session.column_counts["Weekly_Maintenance"] == len(headers)
    assert dict(zip(headers, data_rows(sheets, "Weekly_Maintenance")[0]))["checklist:check-39"] is True
    assert sheets.check()["schema_issues"] == []


def test_duplicate_projection_rows_keep_newest_and_clear_only_duplicate(sheets, event):
    sheets.sync(event, [])
    row = deepcopy(data_rows(sheets, "Weekly_Maintenance")[0])
    row[1] = 5
    sheets.session.tabs["Weekly_Maintenance"].append(row)
    unrelated = deepcopy(row)
    unrelated[0] = "weekly-untouched"
    sheets.session.tabs["Weekly_Maintenance"].append(unrelated)
    sheets.sync(event, [])
    actual = data_rows(sheets, "Weekly_Maintenance")
    assert len(actual) == 2
    assert actual[0][1] == 5
    assert actual[1] == unrelated


def test_conflicting_audit_change_id_is_not_overwritten(sheets, event):
    sheets.sync(event, [])
    event["record"]["note"] = "Changed without changing ID"
    with pytest.raises(IntegrationError, match="conflicting content"):
        sheets.sync(event, [])
    assert "Changed without" not in data_rows(sheets, "Activity_Log")[0][7]


def test_existing_unrelated_sheet_columns_are_never_overwritten(sheets, event):
    original = [["My unrelated data"], ["preserve this"]]
    sheets.session.tabs["Weekly_Maintenance"] = deepcopy(original)
    with pytest.raises(IntegrationError, match="unexpected columns"):
        sheets.sync(event, [])
    assert sheets.session.tabs["Weekly_Maintenance"] == original


def test_test_write_changes_only_its_own_isolated_test_row(sheets):
    sheets.session.tabs["Weekly_Maintenance"] = [["do not touch"], ["production record"]]
    other_test = ["other-kiosk-test", "TEST", "earlier"]
    sheets.session.tabs[TEST_TAB] = [TEST_HEADERS[:], other_test[:]]
    before = deepcopy(sheets.session.tabs["Weekly_Maintenance"])
    result = sheets.test_write()
    assert result["verified"] is True and result["cleaned_up"] is True
    assert sheets.session.tabs["Weekly_Maintenance"] == before
    assert data_rows(sheets, TEST_TAB) == [other_test]
    assert "Activity_Log" not in sheets.session.tabs


def test_test_write_ambiguous_append_still_cleans_up(sheets):
    sheets.session.fail_after_append = TEST_TAB
    with pytest.raises(IntegrationError, match="timed out"):
        sheets.test_write()
    assert data_rows(sheets, TEST_TAB) == []


def test_test_write_reports_cleanup_failure(sheets):
    sheets.session.fail_cleanup = True
    with pytest.raises(IntegrationError, match="cleanup could not be confirmed") as caught:
        sheets.test_write()
    assert "kiosk-test-" in str(caught.value)
    assert len(data_rows(sheets, TEST_TAB)) == 1


def test_google_credentials_reject_custom_token_server(monkeypatch):
    called = []
    monkeypatch.setattr(integration, "AuthorizedSession", lambda *args, **kwargs: called.append(args))
    for mode, secret in [("service_account", "google_service_account"), ("oauth", "google_oauth_token")]:
        config = {"google": {"spreadsheet_id": "spreadsheet-12345678", "auth_mode": mode}}
        data = {"type": "service_account", "token_uri": "https://attacker.invalid/token"}
        with pytest.raises(IntegrationError, match="credentials are missing or invalid"):
            GoogleSheets(config, {secret: json.dumps(data)})
    assert called == []


def test_google_authorized_user_credentials_use_official_scopes(monkeypatch):
    calls = []
    marker = object()
    monkeypatch.setattr(integration.user_credentials.Credentials, "from_authorized_user_info",
                        lambda info, scopes: calls.append((info, scopes)) or marker)
    monkeypatch.setattr(integration, "AuthorizedSession", lambda creds, **kwargs: {"credentials": creds})
    adapter = GoogleSheets({"google": {"spreadsheet_id": "spreadsheet-12345678", "auth_mode": "oauth"}},
                           {"google_oauth_token": json.dumps({"refresh_token": "secret", "client_id": "client", "client_secret": "private"})})
    assert calls[0][1] == integration.SCOPES
    assert adapter.session["credentials"] is marker


def test_google_oauth_preserves_granted_scopes_and_bounds_refresh(monkeypatch):
    captured = {}
    marker = object()

    def credentials(info, scopes):
        captured["scopes"] = scopes
        return marker

    def session(creds, **kwargs):
        captured["refresh_timeout"] = kwargs.get("refresh_timeout")
        return marker

    monkeypatch.setattr(integration.user_credentials.Credentials, "from_authorized_user_info", credentials)
    monkeypatch.setattr(integration, "AuthorizedSession", session)
    granted = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive.file"]
    GoogleSheets({"google": {"spreadsheet_id": "spreadsheet-12345678", "auth_mode": "oauth"}},
                 {"google_oauth_token": json.dumps({"scopes": granted})})
    assert captured["scopes"] == granted
    assert captured["refresh_timeout"] == 20


def test_delayed_notification_includes_current_state_near_start(event):
    event["current_state"] = {"state": "resolved", "availability": "available"}
    event["spreadsheet_id"] = "spreadsheet-12345678"
    event["record"]["note"] = "x" * 1000
    message = integration.build_message(event)
    assert "Current printer availability: available" in message[:200]
    assert "State at event: completed" in message
    assert "https://docs.google.com/spreadsheets/d/spreadsheet-12345678" in message


DISCORD_URL = "https://discord.com/api/webhooks/123456789012345678/secret-webhook-token-123456789"


@pytest.mark.parametrize("url", [
    "http://discord.com/api/webhooks/123456/token-value-abc",
    "https://discord.com.evil.invalid/api/webhooks/123456/token-value-abc",
    "https://discord.com@evil.invalid/api/webhooks/123456/token-value-abc",
    "https://127.0.0.1/api/webhooks/123456/token-value-abc",
    "https://discord.com/api/webhooks/123456/token-value-abc?redirect=evil",
    "https://discord.com/api/webhooks/123456/token-value-abc#fragment",
    "https://discord.com:80/api/webhooks/123456/token-value-abc",
    "https://discord.com/api/webhooks/123456/token-value-abc/messages/999",
])
def test_discord_rejects_nonwebhook_destinations_without_network(monkeypatch, url):
    monkeypatch.setattr(integration.requests, "post", lambda *args, **kwargs: pytest.fail("Network must not be called"))
    with pytest.raises(IntegrationError):
        integration.send_discord({}, url, {"message": "test"}, test=True)


def test_discord_waits_for_message_id_and_suppresses_all_mentions(monkeypatch, event):
    calls = []
    monkeypatch.setattr(integration.requests, "post", lambda url, **kwargs:
                        calls.append((url, kwargs)) or Response(data={"id": "123", "channel_id": "456"}))
    event["record"]["note"] = "@everyone <@&123>"
    result = integration.send_discord({"id": "alerts"}, DISCORD_URL, event, test=True)
    assert result["message_id"] == "123"
    assert result["channel_id"] == "456"
    url, options = calls[0]
    assert "wait=true" in url
    assert options["allow_redirects"] is False
    assert options["json"]["content"].startswith("[TEST]")
    assert options["json"]["allowed_mentions"]["parse"] == []
    assert options["json"]["allowed_mentions"]["roles"] == []
    assert options["timeout"] == integration.TIMEOUT
    assert "verify" not in options  # Requests retains certificate verification by default.


def test_discord_rate_limit_includes_safe_retry_delay(monkeypatch):
    monkeypatch.setattr(integration.requests, "post", lambda *args, **kwargs:
                        Response(429, {"retry_after": 1.25, "message": DISCORD_URL}))
    with pytest.raises(IntegrationError) as caught:
        integration.send_discord({}, DISCORD_URL, {})
    assert caught.value.retryable is True
    assert caught.value.retry_after == 1.25
    assert "secret-webhook" not in str(caught.value)


def test_discord_timeout_never_leaks_credential_url(monkeypatch):
    def timeout(*args, **kwargs):
        raise requests.Timeout(DISCORD_URL)
    monkeypatch.setattr(integration.requests, "post", timeout)
    with pytest.raises(IntegrationError) as caught:
        integration.send_discord({}, DISCORD_URL, {})
    assert caught.value.retryable is True
    assert "secret-webhook" not in str(caught.value)


def test_discord_tls_failure_is_permanent_and_not_bypassed(monkeypatch):
    def tls_error(*args, **kwargs):
        raise requests.exceptions.SSLError(DISCORD_URL)
    monkeypatch.setattr(integration.requests, "post", tls_error)
    with pytest.raises(IntegrationError) as caught:
        integration.send_discord({}, DISCORD_URL, {})
    assert caught.value.retryable is False
    assert "TLS verification" in str(caught.value)


class FakeSMTP:
    def __init__(self):
        self.steps = []
        self.messages = []
        self.refused = {}
        self.login_error = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def ehlo(self):
        self.steps.append("ehlo")

    def starttls(self, context):
        assert context.verify_mode == ssl.CERT_REQUIRED
        assert context.check_hostname is True
        self.steps.append("starttls")

    def login(self, username, password):
        self.steps.append("login")
        if self.login_error:
            raise self.login_error

    def send_message(self, message, **kwargs):
        self.steps.append("send")
        self.messages.append((message, kwargs))
        return self.refused


@pytest.fixture
def email_config():
    return {"host": "smtp.gmail.com", "port": 587, "security": "starttls", "sender": "farm@example.com",
            "recipients": ["technician@example.com"], "username": "farm@example.com", "subject_prefix": "[iForge]"}


def test_email_tls_precedes_login_and_reports_acceptance_only(monkeypatch, email_config, event):
    smtp = FakeSMTP()
    monkeypatch.setattr(integration.smtplib, "SMTP", lambda *args, **kwargs: smtp)
    result = integration.send_email(email_config, "secret", event, test=True)
    assert smtp.steps == ["ehlo", "starttls", "ehlo", "login", "send"]
    assert result["accepted"] is True
    assert result["delivery_confirmed"] is False
    assert smtp.messages[0][0]["Subject"].startswith("[TEST]")
    assert smtp.messages[0][1]["to_addrs"] == ["technician@example.com"]


def test_email_auth_error_is_redacted_and_not_retried(monkeypatch, email_config):
    smtp = FakeSMTP()
    smtp.login_error = smtplib.SMTPAuthenticationError(535, b"secret-password")
    monkeypatch.setattr(integration.smtplib, "SMTP", lambda *args, **kwargs: smtp)
    with pytest.raises(IntegrationError) as caught:
        integration.send_email(email_config, "secret-password", {})
    assert caught.value.retryable is False
    assert "secret-password" not in str(caught.value)
    assert "send" not in smtp.steps


def test_email_partial_refusal_does_not_trigger_duplicate_retries(monkeypatch, email_config):
    smtp = FakeSMTP()
    smtp.refused = {"recipient@example.com": (550, b"refused")}
    monkeypatch.setattr(integration.smtplib, "SMTP", lambda *args, **kwargs: smtp)
    with pytest.raises(IntegrationError, match="accepted some recipients") as caught:
        integration.send_email(email_config, "secret", {})
    assert caught.value.retryable is False


def test_email_rejects_plaintext_and_header_injection(monkeypatch, email_config):
    monkeypatch.setattr(integration.smtplib, "SMTP", lambda *args, **kwargs: pytest.fail("Must validate first"))
    email_config["security"] = "none"
    with pytest.raises(IntegrationError, match="unencrypted"):
        integration.send_email(email_config, "secret", {})
    email_config["security"] = "starttls"
    email_config["sender"] = "sender@example.com\r\nBcc: attacker@example.com"
    with pytest.raises(IntegrationError, match="line breaks"):
        integration.send_email(email_config, "secret", {})
