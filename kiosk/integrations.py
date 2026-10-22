"""Bounded, explicit cloud adapters. Importing this module never contacts a service.

Google Sheets is a shared projection of the kiosk journal. A single worker must
serialize calls to sync(); stable IDs make an interrupted write safe to resume.
Credentials belong in the server-side secret store, never in queued payloads.
"""
from __future__ import annotations

import json
import math
import re
import smtplib
import ssl
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import requests
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import credentials as user_credentials
from google.oauth2 import service_account

TIMEOUT = (5, 20)
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]
SHEETS_ROOT = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE_ROOT = "https://www.googleapis.com/drive/v3/files"
HEADERS = {
    "Printers": [
        "printer_id", "name", "model", "active", "availability", "open_incident_id",
        "last_weekly_at", "last_reactive_at", "latest_fix_summary", "current_fault_summary",
    ],
    "Weekly_Maintenance": [
        "record_id", "revision", "printer_id", "printer_name", "technician", "academic_year",
        "semester", "week", "state", "checklist_json", "checklist_definitions_json",
        "metrics_json", "metric_definitions_json", "settings_revision", "note", "created_at",
        "updated_at", "completed_at",
    ],
    "Reactive_Maintenance": [
        "record_id", "revision", "printer_id", "printer_name", "technician", "source", "symptom",
        "summary", "action_taken", "component", "state", "result_status", "fix_summary",
        "availability", "note", "diagnosis_path_json", "sop_ids_json", "opened_at", "updated_at",
        "resolved_at",
    ],
    "Activity_Log": [
        "change_id", "record_id", "revision", "event_type", "actor", "occurred_at",
        "synced_at", "record_snapshot_json",
    ],
}
TEST_TAB = "_Kiosk_Tests"
TEST_HEADERS = ["test_id", "marker", "created_at"]


class IntegrationError(Exception):
    """A safe operator-facing error; never include raw remote error bodies."""

    def __init__(self, message, retryable=False, retry_after=None):
        super().__init__(message)
        self.retryable = bool(retryable)
        self.retry_after = retry_after


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _retry_after(value):
    try:
        delay = float(value)
        return min(max(delay, 0), 86400) if math.isfinite(delay) else None
    except (ValueError, TypeError):
        return None


def _text(value, limit=1000):
    """Limit free-form fields without ever rendering markup or headers."""
    if value is None:
        return ""
    return str(value).replace("\x00", "")[:limit]


def build_message(payload, test=False):
    record = payload.get("record") or {}
    event_type = _text(payload.get("event_type", "maintenance_update"), 80).replace("_", " ")
    lines = [f"{'[TEST] ' if test else ''}Maintenance: {event_type}"]
    if payload.get("message"):
        lines.append(_text(payload["message"], 1200))
    current = payload.get("current_state")
    if isinstance(current, dict):
        if current.get("state"):
            lines.append(f"Current incident state: {_text(current['state'], 80)}")
        if current.get("availability"):
            lines.append(f"Current printer availability: {_text(current['availability'], 80)}")
    for label, value in [
        ("Printer", record.get("printer_name") or record.get("printer_id")),
        ("Record", record.get("id")),
        ("Occurred", payload.get("occurred_at") or record.get("updated_at")),
        ("State at event", record.get("state")),
        ("Availability at event", record.get("availability")),
        ("Technician", payload.get("technician_name", record.get("technician"))),
        ("Issue", record.get("symptom") or record.get("summary")),
        ("Action", record.get("fix_summary") or record.get("action_taken")),
        ("Notes", record.get("note")),
    ]:
        if value not in (None, ""):
            lines.append(f"{label}: {_text(value, 600)}")
    # This wording stays accurate for an alert delayed by a network outage.
    if record:
        lines.append("This describes the recorded event. Check the kiosk for current availability.")
    spreadsheet_id = str(payload.get("spreadsheet_id") or "")
    if re.fullmatch(r"[A-Za-z0-9_-]{10,200}", spreadsheet_id):
        lines.append(f"Maintenance workbook: https://docs.google.com/spreadsheets/d/{spreadsheet_id}")
    if test:
        lines.append("Test only: no maintenance incident or printer state was changed.")
    return "\n".join(lines)


def _email_address(value):
    value = str(value or "").strip()
    if "\r" in value or "\n" in value:
        raise IntegrationError("Email addresses must not contain line breaks.")
    _, address = parseaddr(value)
    if not re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", address):
        raise IntegrationError("Configure a valid sender and recipient email address.")
    return value


def send_email(config, password, payload, test=False):
    """Return SMTP acceptance, which does not prove delivery to an inbox."""
    host = str(config.get("host") or "").strip()
    if not host or any(character.isspace() for character in host) or "/" in host:
        raise IntegrationError("Configure an SMTP hostname before sending email.")
    try:
        port = int(config.get("port", 587))
    except (ValueError, TypeError):
        raise IntegrationError("Configure a valid SMTP port.") from None
    if not 1 <= port <= 65535:
        raise IntegrationError("Configure a valid SMTP port.")
    security = config.get("security", "starttls")
    if security not in {"starttls", "ssl", "tls"}:
        raise IntegrationError("SMTP requires STARTTLS or TLS; unencrypted email is unsupported.")
    sender = _email_address(config.get("sender"))
    recipients = config.get("recipients") or []
    if not isinstance(recipients, list) or not recipients:
        raise IntegrationError("Select at least one email recipient.")
    recipients = [_email_address(address) for address in recipients]
    username = str(config.get("username") or "")
    if username and not password:
        raise IntegrationError("Configure the SMTP password before sending email.")
    prefix = str(config.get("subject_prefix") or "[iForge]")
    if "\r" in prefix or "\n" in prefix:
        raise IntegrationError("The email subject prefix must be a single line.")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    event_name = _text(payload.get("event_type", "maintenance update"), 80).replace("_", " ")
    event_name = event_name.replace("\r", " ").replace("\n", " ")
    message["Subject"] = f"{'[TEST] ' if test else ''}{prefix} {event_name}"
    message.set_content(build_message(payload, test=test))
    context = ssl.create_default_context()
    try:
        factory = smtplib.SMTP if security == "starttls" else smtplib.SMTP_SSL
        kwargs = {"timeout": 20}
        if security != "starttls":
            kwargs["context"] = context
        with factory(host, port, **kwargs) as client:
            client.ehlo()
            if security == "starttls":
                client.starttls(context=context)
                client.ehlo()
            if username:
                client.login(username, password)
            refused = client.send_message(message, from_addr=parseaddr(sender)[1],
                                          to_addrs=[parseaddr(address)[1] for address in recipients])
            if refused:
                # A retry could duplicate delivery to the recipients who accepted it.
                raise IntegrationError("SMTP accepted some recipients but refused others; check the recipient list before retrying.")
    except IntegrationError:
        raise
    except smtplib.SMTPAuthenticationError:
        raise IntegrationError("SMTP authentication failed; check the username and password.") from None
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused):
        raise IntegrationError("SMTP refused the sender or recipients; check email settings.") from None
    except smtplib.SMTPNotSupportedError:
        raise IntegrationError("The SMTP server does not support the required secure operation.") from None
    except ssl.SSLError:
        raise IntegrationError("SMTP TLS verification failed; check the server certificate and hostname.") from None
    except smtplib.SMTPResponseException as exc:
        raise IntegrationError("The SMTP server rejected the request.",
                               retryable=400 <= exc.smtp_code < 500) from None
    except (smtplib.SMTPException, OSError):
        raise IntegrationError("SMTP connection failed or timed out. A timeout after acceptance can cause a duplicate on retry.",
                               retryable=True) from None
    return {"accepted": True, "recipients": recipients, "transport": security,
            "delivery_confirmed": False, "test": bool(test)}


def _discord_url(webhook_url):
    try:
        parsed = urlsplit(str(webhook_url or ""))
        port = parsed.port
    except ValueError:
        raise IntegrationError("Configure a valid official Discord webhook URL.") from None
    allowed_hosts = {"discord.com", "discordapp.com", "canary.discord.com", "ptb.discord.com"}
    if (parsed.scheme != "https" or parsed.hostname not in allowed_hosts
            or parsed.username or parsed.password or port not in (None, 443) or parsed.fragment
            or not re.fullmatch(r"/api(?:/v\d+)?/webhooks/\d{5,30}/[A-Za-z0-9._-]{10,}/?", parsed.path)):
        raise IntegrationError("Use an HTTPS webhook URL from an official Discord domain.")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if any(key not in {"wait", "thread_id"} for key in query):
        raise IntegrationError("Discord webhook URLs support only wait and thread_id parameters.")
    if "thread_id" in query and (len(query["thread_id"]) != 1 or not query["thread_id"][0].isdigit()):
        raise IntegrationError("The Discord thread ID must be numeric.")
    params = {"wait": "true"}
    if "thread_id" in query:
        params["thread_id"] = query["thread_id"][0]
    return urlunsplit(("https", parsed.netloc, parsed.path, urlencode(params), ""))


def send_discord(config, webhook_url, payload, test=False):
    url = _discord_url(webhook_url)
    text = build_message(payload, test=test)
    if len(text) > 1950:
        text = text[:1920] + "\n[Message truncated]"
    body = {"content": text, "allowed_mentions": {"parse": [], "roles": [], "users": [], "replied_user": False}}
    try:
        response = requests.post(url, json=body, timeout=TIMEOUT, allow_redirects=False)
    except requests.exceptions.SSLError:
        raise IntegrationError("Discord TLS verification failed.") from None
    except requests.RequestException:
        raise IntegrationError("Discord connection failed or timed out. A timeout after acceptance can cause a duplicate on retry.",
                               retryable=True) from None
    try:
        data = response.json()
        if not isinstance(data, dict):
            data = {}
    except (ValueError, requests.RequestException):
        data = {}
    if response.status_code == 429:
        delay = _retry_after(data.get("retry_after"))
        if delay is None:
            delay = _retry_after(response.headers.get("Retry-After"))
        raise IntegrationError("Discord rate limit reached; delivery will retry later.", True, delay)
    if response.status_code in {401, 403, 404}:
        raise IntegrationError("Discord refused this webhook; check or replace its URL in Settings.")
    if not 200 <= response.status_code < 300:
        raise IntegrationError(f"Discord returned HTTP {response.status_code}.",
                               retryable=response.status_code >= 500 or response.status_code == 408)
    if not data.get("id"):
        raise IntegrationError("Discord accepted the request but returned no message ID. Check the channel before retrying.")
    return {"accepted": True, "message_id": str(data["id"]), "channel_id": str(data.get("channel_id", "")),
            "destination_id": str(config.get("id", "")), "test": bool(test)}


class GoogleSheets:
    """Explicitly initialized Sheets projection with stable-ID reconciliation."""

    def __init__(self, settings, secrets):
        self.settings = settings
        self.config = settings.get("google", {})
        self.spreadsheet_id = str(self.config.get("spreadsheet_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", self.spreadsheet_id):
            raise IntegrationError("Set the Google spreadsheet ID in Settings.")
        self.base_url = f"{SHEETS_ROOT}/{self.spreadsheet_id}"
        self.auth_mode = self.config.get("auth_mode", "service_account")
        self.credential_email = None
        try:
            if self.auth_mode == "service_account":
                info = json.loads(secrets.get("google_service_account") or "{}")
                if not isinstance(info, dict) or info.get("type") != "service_account":
                    raise ValueError("Missing service account")
                if info.get("token_uri") != "https://oauth2.googleapis.com/token":
                    raise ValueError("Unsupported token endpoint")
                creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
                self.credential_email = info.get("client_email")
            elif self.auth_mode in {"oauth", "oauth_user", "authorized_user"}:
                info = json.loads(secrets.get("google_oauth_token") or "{}")
                if not isinstance(info, dict):
                    raise ValueError("Missing OAuth credentials")
                if info.get("token_uri", "https://oauth2.googleapis.com/token") != "https://oauth2.googleapis.com/token":
                    raise ValueError("Unsupported token endpoint")
                scopes = info.get("scopes") or SCOPES
                if isinstance(scopes, str):
                    scopes = scopes.split()
                creds = user_credentials.Credentials.from_authorized_user_info(info, scopes=scopes)
            else:
                raise ValueError("Unsupported authorization mode")
            self.session = AuthorizedSession(creds, refresh_timeout=20)
        except (ValueError, TypeError, KeyError, GoogleAuthError):
            raise IntegrationError("Google credentials are missing or invalid; reconnect Google in Settings.") from None
        self._drive_checked = False

    def _request(self, method, url, **kwargs):
        try:
            response = self.session.request(method, url, timeout=TIMEOUT, allow_redirects=False, **kwargs)
        except requests.exceptions.SSLError:
            raise IntegrationError("Google TLS verification failed.") from None
        except GoogleAuthError:
            raise IntegrationError("Google authorization failed; reconnect the account in Settings.") from None
        except requests.RequestException:
            raise IntegrationError("Google connection failed or timed out; queued records remain saved locally.", True) from None
        if not 200 <= response.status_code < 300:
            message = f"Google returned HTTP {response.status_code}."
            if response.status_code in {401, 403}:
                message += " Check credentials, API access, and sharing permissions."
            elif response.status_code == 404:
                message += " Check the workbook or folder ID and its sharing permissions."
            error = IntegrationError(message, response.status_code in {408, 429} or response.status_code >= 500,
                                     _retry_after(response.headers.get("Retry-After")))
            error.http_status = response.status_code
            raise error
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("Expected object")
            return data
        except ValueError:
            raise IntegrationError("Google returned an unreadable response; the operation may have completed.", True) from None

    def _metadata(self):
        return self._request("GET", self.base_url,
                             params={"fields": "spreadsheetId,properties(title),sheets(properties(sheetId,title,gridProperties(columnCount)))"})

    def _verify_drive(self):
        result = {"owner_verified": False, "owners": [], "folder_verified": None, "warnings": []}
        expected = str(self.config.get("account_email") or "").strip().lower()
        try:
            metadata = self._request("GET", f"{DRIVE_ROOT}/{self.spreadsheet_id}",
                                     params={"fields": "id,name,mimeType,owners(emailAddress),parents,capabilities(canEdit)",
                                             "supportsAllDrives": "true"})
        except IntegrationError as exc:
            if getattr(exc, "http_status", None) != 403:
                raise
            result["warnings"].append("Workbook ownership could not be verified. Enable Google Drive API and metadata access.")
            return result
        if metadata.get("mimeType") != "application/vnd.google-apps.spreadsheet":
            raise IntegrationError("The configured Google file is not a Google Sheets workbook.")
        owners = [owner.get("emailAddress", "").lower() for owner in metadata.get("owners", []) if owner.get("emailAddress")]
        result["owners"] = owners
        if expected and owners and expected not in owners:
            raise IntegrationError("The workbook owner does not match the Google account configured in Settings.")
        result["owner_verified"] = bool(expected and expected in owners)
        if not result["owner_verified"]:
            result["warnings"].append("Google did not return a matching individual workbook owner; confirm ownership in Drive.")
        if metadata.get("capabilities", {}).get("canEdit") is False:
            raise IntegrationError("The connected Google account cannot edit this workbook. Grant it Editor access.")
        folder_id = str(self.config.get("folder_id") or "").strip()
        if folder_id:
            if not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", folder_id):
                raise IntegrationError("Set a valid Google Drive folder ID.")
            folder = self._request("GET", f"{DRIVE_ROOT}/{folder_id}",
                                   params={"fields": "id,mimeType,owners(emailAddress)", "supportsAllDrives": "true"})
            if folder.get("mimeType") != "application/vnd.google-apps.folder":
                raise IntegrationError("The configured Google Drive folder ID does not identify a folder.")
            folder_owners = [owner.get("emailAddress", "").lower() for owner in folder.get("owners", [])]
            if expected and folder_owners and expected not in folder_owners:
                raise IntegrationError("The selected Drive folder owner does not match the configured Google account.")
            if folder_id not in metadata.get("parents", []):
                raise IntegrationError("The maintenance workbook is not in the selected Google Drive folder.")
            result["folder_verified"] = True
        self._drive_checked = result["owner_verified"] and (not folder_id or result["folder_verified"] is True)
        return result

    def _read(self, tab, cell_range=None):
        name = quote(f"'{tab}'" + (f"!{cell_range}" if cell_range else ""), safe="")
        return self._request("GET", f"{self.base_url}/values/{name}",
                             params={"valueRenderOption": "UNFORMATTED_VALUE"}).get("values", [])

    def _write(self, tab, row_index, values):
        name = quote(f"'{tab}'!A{row_index}", safe="")
        return self._request("PUT", f"{self.base_url}/values/{name}",
                             params={"valueInputOption": "RAW"}, json={"values": [values]})

    def _append(self, tab, values):
        name = quote(f"'{tab}'", safe="")
        return self._request("POST", f"{self.base_url}/values/{name}:append",
                             params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
                             json={"values": [values]})

    def _clear_rows(self, tab, row_numbers):
        if row_numbers:
            self._request("POST", f"{self.base_url}/values:batchClear",
                          json={"ranges": [f"'{tab}'!{row}:{row}" for row in row_numbers]})

    @staticmethod
    def _valid_headers(tab, actual, expected):
        if actual == expected:
            return True
        if tab != "Weekly_Maintenance" or actual[:len(expected)] != expected:
            return False
        suffix = actual[len(expected):]
        return (len(actual) == len(set(actual)) and all(
            isinstance(column, str) and re.fullmatch(r"(?:checklist|metric|unit):[A-Za-z0-9_-]{1,100}", column)
            for column in suffix))

    def check(self):
        """Read-only check: no creation, row edits, email, or notifications."""
        metadata = self._metadata()
        drive = self._verify_drive()
        titles = {sheet["properties"]["title"] for sheet in metadata.get("sheets", [])}
        missing = [tab for tab in HEADERS if tab not in titles]
        schema_issues = []
        for tab, expected in HEADERS.items():
            if tab in titles:
                rows = self._read(tab, "1:1")
                actual = rows[0] if rows else []
                if actual and not self._valid_headers(tab, actual, expected):
                    schema_issues.append(tab)
        if schema_issues:
            raise IntegrationError("Workbook column headers do not match the kiosk schema: " + ", ".join(schema_issues) + ". Select a dedicated workbook; existing columns will not be overwritten.")
        return {"connected": True, "account_email": self.config.get("account_email"),
                "credential_type": self.auth_mode, "credential_email": self.credential_email,
                "spreadsheet_id": self.spreadsheet_id,
                "spreadsheet_title": metadata.get("properties", {}).get("title", ""),
                "missing_tabs": missing, "schema_issues": schema_issues, **drive}

    def _ensure_tabs(self, schemas):
        if not self._drive_checked:
            verification = self._verify_drive()
            if not verification["owner_verified"] or (self.config.get("folder_id") and verification["folder_verified"] is not True):
                raise IntegrationError("Google workbook ownership could not be verified. Enable Drive metadata access and verify the configured owner before any writes.")
        metadata = self._metadata()
        titles = {sheet["properties"]["title"] for sheet in metadata.get("sheets", [])}
        missing = [name for name in schemas if name not in titles]
        if missing:
            self._request("POST", f"{self.base_url}:batchUpdate",
                          json={"requests": [{"addSheet": {"properties": {"title": name}}} for name in missing]})
        for tab, headers in schemas.items():
            rows = self._read(tab)
            if rows and any(any(value not in (None, "") for value in row) for row in rows):
                if not self._valid_headers(tab, rows[0], headers):
                    raise IntegrationError(f"The {tab} tab has unexpected columns. Its existing data was left unchanged.")
            else:
                self._write(tab, 1, headers)

    @staticmethod
    def _current_row(record, headers=None):
        if record.get("kind") == "weekly":
            tab = "Weekly_Maintenance"
        elif record.get("kind") in {"reactive", "incident"}:
            tab = "Reactive_Maintenance"
        else:
            raise IntegrationError("The maintenance record has an unsupported kind.")
        values = []
        units = {item["id"]: item.get("unit", "") for item in record.get("metric_definitions", [])}
        for key in headers or HEADERS[tab]:
            if ":" in key:
                category, identifier = key.split(":", 1)
                if category == "unit":
                    value = units.get(identifier, "")
                else:
                    field = "checklist" if category == "checklist" else "metrics"
                    value = record.get(field, {}).get(identifier, "")
                values.append(value)
                continue
            source = "id" if key == "record_id" else key.removesuffix("_json")
            value = record.get(source, "")
            if key.endswith("_json"):
                value = _json(value if value != "" else {})
            values.append("" if value is None else value)
        return tab, values

    def _weekly_columns(self, record):
        """Extend stable named columns while retaining every earlier definition."""
        tab = "Weekly_Maintenance"
        headers = list(self._read(tab, "1:1")[0])
        requested = []
        for definition in record.get("checklist_definitions", []):
            requested.append(f"checklist:{definition['id']}")
        for definition in record.get("metric_definitions", []):
            requested.extend([f"metric:{definition['id']}", f"unit:{definition['id']}"])
        for column in requested:
            if not re.fullmatch(r"(?:checklist|metric|unit):[A-Za-z0-9_-]{1,100}", column):
                raise IntegrationError("A weekly maintenance field has an invalid stable ID.")
            if column not in headers:
                headers.append(column)
        metadata = self._metadata()
        properties = next(sheet["properties"] for sheet in metadata["sheets"] if sheet["properties"]["title"] == tab)
        sheet_id = properties["sheetId"]
        requests_body = []
        if properties.get("gridProperties", {}).get("columnCount", 26) < len(headers):
            requests_body.append({"updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"columnCount": len(headers)}},
                "fields": "gridProperties.columnCount"}})
        # Reapply formatting during retries, including a crash after header extension.
        # RAW boolean/numeric values remain directly usable by Sheets filters/charts.
        for index, column in enumerate(headers):
            column_range = {"sheetId": sheet_id, "startRowIndex": 1,
                            "startColumnIndex": index, "endColumnIndex": index + 1}
            if column.startswith("checklist:"):
                requests_body.append({"setDataValidation": {"range": column_range, "rule": {
                    "condition": {"type": "BOOLEAN"}, "strict": True, "showCustomUi": True}}})
            elif column.startswith("metric:"):
                requests_body.append({"repeatCell": {"range": column_range, "cell": {
                    "userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "0.###"}}},
                    "fields": "userEnteredFormat.numberFormat"}})
        if requests_body:
            self._request("POST", f"{self.base_url}:batchUpdate", json={"requests": requests_body})
        if headers != self._read(tab, "1:1")[0]:
            self._write(tab, 1, headers)
        return headers

    def _upsert(self, tab, values, revision_column=None):
        rows = self._read(tab)
        matches = [(index, row) for index, row in enumerate(rows[1:], 2) if row and str(row[0]) == str(values[0])]
        if not matches:
            self._append(tab, values)
            return "created"
        index, existing = matches[0]
        if revision_column is not None:
            try:
                # If a previous ambiguous append produced duplicates, retain the newest revision.
                index, existing = max(matches, key=lambda item: int(item[1][revision_column]))
                existing_revision = int(existing[revision_column])
                incoming_revision = int(values[revision_column])
            except (ValueError, TypeError, IndexError):
                raise IntegrationError(f"The {tab} tab contains an invalid revision; review the workbook before syncing.") from None
            if existing_revision <= incoming_revision:
                self._write(tab, index, values)
            result = "updated" if existing_revision <= incoming_revision else "newer_revision_preserved"
        else:
            self._write(tab, index, values)
            result = "updated"
        self._clear_rows(tab, [row_index for row_index, _ in matches if row_index != index])
        return result

    def sync(self, event, printers):
        record = event.get("record") or {}
        change_id = event.get("change_id")
        if not change_id or not record.get("id") or not isinstance(record.get("revision"), int):
            raise IntegrationError("A Sheets change requires stable change/record IDs and an integer revision.")
        tab, projection = self._current_row(record)
        snapshot = _json(record)
        if len(snapshot) > 49000 or any(isinstance(value, str) and len(value) > 49000 for value in projection):
            raise IntegrationError("This record exceeds the Google Sheets cell size limit; reduce long notes or attachments.")
        self._ensure_tabs(HEADERS)
        if tab == "Weekly_Maintenance":
            tab, projection = self._current_row(record, self._weekly_columns(record))
        rows = self._read("Activity_Log")
        found = [(index, row) for index, row in enumerate(rows[1:], 2) if row and str(row[0]) == str(change_id)]
        for _, row in found:
            try:
                matches = (str(row[1]) == str(record["id"]) and int(row[2]) == record["revision"]
                           and json.loads(row[7]) == record)
            except (ValueError, TypeError, IndexError):
                matches = False
            if not matches:
                raise IntegrationError("A change ID in Activity_Log has conflicting content. Review the workbook before retrying.")
        if not found:
            self._append("Activity_Log", [change_id, record["id"], record["revision"], event.get("event_type", ""),
                                          event.get("actor", record.get("technician", "")),
                                          event.get("occurred_at", record.get("updated_at", "")),
                                          _utc_now(), snapshot])
        elif len(found) > 1:
            self._clear_rows("Activity_Log", [index for index, _ in found[1:]])
        result = self._upsert(tab, projection, revision_column=1)
        # The caller supplies current summaries, not historical event summaries.
        for printer in printers:
            values = [printer.get("id") or printer.get("printer_id")]
            if not values[0]:
                raise IntegrationError("A printer summary is missing its stable printer ID.")
            for key in HEADERS["Printers"][1:]:
                value = printer.get(key, "")
                values.append("" if value is None else value)
            self._upsert("Printers", values)
        return {"synced": True, "change_id": change_id, "record_id": record["id"],
                "projection": result, "audit_already_present": bool(found), "spreadsheet_id": self.spreadsheet_id}

    def test_write(self):
        """Write/read/clear only a uniquely identified row in a dedicated test tab."""
        self._ensure_tabs({TEST_TAB: TEST_HEADERS})
        test_id = f"kiosk-test-{uuid4()}"
        row = [test_id, "TEST ONLY — safe to remove", _utc_now()]
        primary_error = None
        try:
            self._append(TEST_TAB, row)
            matches = [value for value in self._read(TEST_TAB)[1:] if value and value[0] == test_id]
            if not matches or any(value != row for value in matches):
                raise IntegrationError("The Google Sheets test row could not be verified.")
        except IntegrationError as exc:
            primary_error = exc
        try:
            matches = [index for index, value in enumerate(self._read(TEST_TAB)[1:], 2) if value and value[0] == test_id]
            self._clear_rows(TEST_TAB, matches)
            if any(value and value[0] == test_id for value in self._read(TEST_TAB)[1:]):
                raise IntegrationError("The test row remains after cleanup.")
        except IntegrationError:
            raise IntegrationError(f"Google Sheets test cleanup could not be confirmed. Inspect only test ID {test_id} in {TEST_TAB}; production records were not changed.") from None
        if primary_error:
            raise primary_error
        return {"verified": True, "cleaned_up": True, "test_id": test_id, "tab": TEST_TAB,
                "spreadsheet_id": self.spreadsheet_id}
