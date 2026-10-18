"""Validated maintenance workflows and durable local delivery queues."""

from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .defaults import DEFAULT_SETTINGS
from .storage import Journal, atomic_json


EVENT_TYPES = {"fault_opened", "incident_updated", "incident_escalated", "incident_resolved", "weekly_saved", "weekly_completed", "maintenance_overdue"}
ACTIVE_INCIDENTS = {"open", "in_progress", "escalated"}
SECRET_NAMES = {"google_service_account", "google_oauth_client", "google_oauth_token", "smtp_password"}
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$")


def now():
    return datetime.now(timezone.utc).isoformat()


def _text(value, label, required=False, limit=10000):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError(f"{label} must be {'non-empty ' if required else ''}text, at most {limit} characters.")
    return value.strip()


def _number(value, label, minimum=0, maximum=1e12, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum or not math.isfinite(value) or (integer and not isinstance(value, int)):
        raise ValueError(f"{label} must be {'an integer' if integer else 'a number'} between {minimum} and {maximum}.")
    return value


def _bool(value, label):
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be true or false.")


def _keys(value, allowed, label, required=()):
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object.")
    if set(value) - set(allowed):
        raise ValueError(f"{label} contains unsupported fields. Use the separate connection controls for credentials.")
    if set(required) - set(value):
        raise ValueError(f"{label} is missing required fields.")


def _list(value, label, maximum=500):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"{label} must be a list with at most {maximum} entries.")


def _identifier(value, label):
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must use letters, numbers, underscores or hyphens (maximum 100 characters).")


def _choice(value, choices, label):
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"{label} must be one of: {', '.join(sorted(choices))}.")


def _record_identity(payload):
    _identifier(payload["printer_id"], "Printer ID")
    if "id" in payload and payload["id"] is not None:
        _identifier(payload["id"], "Record ID")
    if "revision" in payload and payload["revision"] is not None:
        _number(payload["revision"], "Record revision", 1, 10**10, True)


def _address(value, label):
    _text(value, label, True, 254)
    if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value):
        raise ValueError(f"{label} must be a valid email address.")


def _events(value):
    _list(value, "Notification events", 20)
    if any(not isinstance(item, str) or item not in EVENT_TYPES for item in value):
        raise ValueError("Unknown notification event.")


def validate_settings(settings):
    _keys(settings, DEFAULT_SETTINGS, "Settings", DEFAULT_SETTINGS)
    _number(settings["revision"], "Settings revision", 1, 10**10, True)
    for section in ("general", "schedule", "google", "email", "discord", "hardware", "backups"):
        _keys(settings[section], DEFAULT_SETTINGS[section], section, DEFAULT_SETTINGS[section])
    general = settings["general"]
    _text(general["farm_name"], "Farm name", True, 200)
    try:
        ZoneInfo(_text(general["timezone"], "Timezone", True, 100))
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Choose a valid IANA timezone, such as Europe/London.") from None
    _number(general["refresh_seconds"], "Refresh interval", 5, 3600, True)
    schemas = {
        "printers": {"id", "name", "model", "active"},
        "technicians": {"id", "name", "active"},
        "checklist": {"id", "label", "required", "active"},
        "metrics": {"id", "label", "unit", "active"},
    }
    ids = {}
    for section, allowed in schemas.items():
        _list(settings[section], section)
        seen = set()
        for item in settings[section]:
            _keys(item, allowed, section, allowed)
            _identifier(item["id"], f"{section} ID")
            if item["id"] in seen:
                raise ValueError(f"Duplicate {section} ID.")
            seen.add(item["id"])
            for key in allowed - {"id", "active", "required"}:
                _text(item[key], f"{section} {key}", True, 200)
            _bool(item["active"], f"{section} active")
            if "required" in item:
                _bool(item["required"], "Checklist required")
        ids[section] = seen
    schedule = settings["schedule"]
    _text(schedule["academic_year"], "Academic year", True, 40)
    _number(schedule["semester"], "Semester", 1, 4, True)
    _number(schedule["teaching_weeks"], "Teaching weeks", 1, 53, True)
    _number(schedule["week"], "Week", 1, schedule["teaching_weeks"], True)
    _bool(schedule["reminders_enabled"], "Reminders enabled")
    google = settings["google"]
    _bool(google["enabled"], "Google enabled")
    _address(google["account_email"], "Google account")
    _choice(google["auth_mode"], {"oauth", "service_account"}, "Google authentication")
    for key in ("spreadsheet_id", "folder_id"):
        _text(google[key], f"Google {key}", limit=200)
        if google[key] and not re.fullmatch(r"[A-Za-z0-9_-]+", google[key]):
            raise ValueError(f"Google {key} must be an ID, not a URL.")
    _number(google["sync_interval"], "Sync interval", 5, 86400, True)
    if google["enabled"] and not google["spreadsheet_id"]:
        raise ValueError("Set a spreadsheet ID before enabling Google sync.")
    email = settings["email"]
    _bool(email["enabled"], "Email enabled")
    for key in ("host", "username", "sender", "subject_prefix"):
        _text(email[key], f"Email {key}", limit=254)
        if "\n" in email[key] or "\r" in email[key]:
            raise ValueError("Email connection fields must be single-line text.")
    if email["host"] and not re.fullmatch(r"[A-Za-z0-9.-]+", email["host"]):
        raise ValueError("Email host must be a hostname.")
    _number(email["port"], "Email port", 1, 65535, True)
    _choice(email["security"], {"starttls", "ssl"}, "Email security")
    if email["sender"]:
        _address(email["sender"], "Email sender")
    _list(email["recipients"], "Email recipients", 100)
    for address in email["recipients"]:
        _address(address, "Email recipient")
    _events(email["events"])
    if email["enabled"] and not (email["host"] and email["sender"] and email["recipients"]):
        raise ValueError("Set the email host, sender and recipients before enabling email.")
    discord = settings["discord"]
    _bool(discord["enabled"], "Discord enabled")
    _list(discord["destinations"], "Discord destinations", 100)
    seen = set()
    for destination in discord["destinations"]:
        fields = {"id", "name", "enabled", "events", "printer_ids"}
        _keys(destination, fields, "Discord destination", fields)
        _identifier(destination["id"], "Discord destination ID")
        if destination["id"] in seen:
            raise ValueError("Duplicate Discord destination ID.")
        seen.add(destination["id"])
        _text(destination["name"], "Discord destination name", True, 200)
        _bool(destination["enabled"], "Discord destination enabled")
        _events(destination["events"])
        _list(destination["printer_ids"], "Discord printer routing")
        if any(not isinstance(value, str) or value not in ids["printers"] for value in destination["printer_ids"]):
            raise ValueError("Discord routing references an unknown printer.")
    hardware = settings["hardware"]
    _choice(hardware["backend"], {"mock", "gpiozero"}, "Hardware backend")
    _number(hardware["hold_seconds"], "Button hold", 0.1, 30)
    _number(hardware["debounce_seconds"], "Button debounce", 0.01, 2)
    _list(hardware["mappings"], "Hardware mappings")
    pins, printers = set(), set()
    for mapping in hardware["mappings"]:
        fields = {"printer_id", "button_pin", "led_pin", "pull_up", "led_active_high"}
        _keys(mapping, fields, "Hardware mapping", fields)
        _identifier(mapping["printer_id"], "Hardware printer ID")
        if mapping["printer_id"] not in ids["printers"] or mapping["printer_id"] in printers:
            raise ValueError("Each hardware mapping needs a unique known printer.")
        printers.add(mapping["printer_id"])
        for key in ("button_pin", "led_pin"):
            _number(mapping[key], key, 0, 27, True)
            if mapping[key] in pins:
                raise ValueError("A GPIO pin may appear in only one input/output mapping.")
            pins.add(mapping[key])
        _bool(mapping["pull_up"], "Button pull-up")
        _bool(mapping["led_active_high"], "LED active-high")
    _bool(settings["backups"]["enabled"], "Backups enabled")
    _number(settings["backups"]["retention"], "Backup retention", 1, 3650, True)


def validate_diagnosis(graph):
    if not isinstance(graph, dict):
        raise ValueError("Diagnosis must be an object.")
    for key in ("categories", "nodes", "sops"):
        if not isinstance(graph.get(key), dict):
            raise ValueError(f"Diagnosis {key} must be an object.")
    nodes, sops = graph["nodes"], graph["sops"]
    if not nodes or not graph["categories"]:
        raise ValueError("Diagnosis needs at least one category and node.")
    if len(nodes) > 1000:
        raise ValueError("Diagnosis supports at most 1000 nodes.")
    edges = {}
    for key, node in nodes.items():
        _identifier(key, "Diagnosis node ID")
        if not isinstance(node, dict):
            raise ValueError("Each diagnosis node must be an object.")
        if node.get("type") == "question":
            _text(node.get("text"), "Question text", True)
            options = node.get("options")
            _list(options, "Question options", 30)
            if not options:
                raise ValueError("Each question needs an answer option.")
            edges[key] = []
            for option in options:
                if not isinstance(option, dict):
                    raise ValueError("Question options must be objects.")
                _text(option.get("label"), "Answer label", True, 500)
                if not isinstance(option.get("next"), str) or option["next"] not in nodes:
                    raise ValueError("Diagnosis contains a broken answer destination.")
                edges[key].append(option["next"])
        elif node.get("type") == "diagnosis":
            _text(node.get("label"), "Diagnosis label", True, 500)
            _list(node.get("sop_ids", []), "SOP references")
            if any(not isinstance(item, str) or item not in sops for item in node.get("sop_ids", [])):
                raise ValueError("Diagnosis references an unknown SOP.")
            edges[key] = []
        else:
            raise ValueError("Node type must be question or diagnosis.")
    roots = []
    for category in graph["categories"].values():
        if not isinstance(category, dict):
            raise ValueError("Diagnosis categories must be objects.")
        _text(category.get("label"), "Category label", True, 500)
        if not isinstance(category.get("start_node"), str) or category["start_node"] not in nodes:
            raise ValueError("Diagnosis category references an unknown starting node.")
        roots.append(category["start_node"])
    for sop in sops.values():
        if not isinstance(sop, dict):
            raise ValueError("SOP definitions must be objects.")
        _text(sop.get("title"), "SOP title", True, 500)
        source = _text(sop.get("file", ""), "SOP file", True, 2000)
        parsed = urlparse(source)
        if not (source.startswith("/sops/") and ".." not in Path(source).parts) and not (parsed.scheme == "https" and parsed.netloc):
            raise ValueError("SOP links must be /sops/ files or HTTPS URLs.")
    # Iterative DFS also handles long user-authored trees without recursion errors.
    visited = set()
    active = set()
    for root in roots:
        stack = [(root, False)]
        while stack:
            key, leaving = stack.pop()
            if leaving:
                active.remove(key)
                visited.add(key)
            elif key in active:
                raise ValueError("Diagnosis graph contains a cycle.")
            elif key not in visited:
                active.add(key)
                stack.append((key, True))
                stack.extend((child, False) for child in reversed(edges[key]))
    if visited != set(nodes):
        raise ValueError("Every diagnosis node must be reachable from a category.")


class KioskService:
    def __init__(self, data_dir, diagnosis_path=None):
        self.data_dir = Path(data_dir)
        self.store = Journal(self.data_dir)
        self.diagnosis_path = Path(diagnosis_path) if diagnosis_path else Path(__file__).resolve().parent.parent / "diagnosis_tree.json"

    def _state(self):
        entries = self.store.read()
        state = {"settings": deepcopy(DEFAULT_SETTINGS), "versions": [], "records": {}, "activity": [], "jobs": {}, "diagnosis": None, "sequence": len(entries)}
        for entry in entries:
            kind = entry.get("type")
            if kind == "settings":
                state["settings"] = entry["settings"]
                state["versions"].append(entry["settings"])
            elif kind == "record":
                event = entry["event"]
                state["records"][event["record"]["id"]] = event["record"]
                state["activity"].append(event)
                for job in entry["jobs"]:
                    state["jobs"][job["id"]] = job
            elif kind == "job":
                state["jobs"][entry["job"]["id"]] = entry["job"]
            elif kind == "diagnosis":
                state["diagnosis"] = entry["diagnosis"]
            else:
                raise RuntimeError("Journal has an unsupported transaction type.")
        return state

    def _commit(self, state, entry):
        self.store.append({"saved_at": now(), **entry}, state["sequence"] + 1)

    def get_settings(self):
        with self.store.locked():
            return deepcopy(self._state()["settings"])

    def settings_history(self):
        with self.store.locked():
            return deepcopy([DEFAULT_SETTINGS, *self._state()["versions"]])

    def save_settings(self, settings):
        settings = deepcopy(settings)
        validate_settings(settings)
        with self.store.locked():
            state = self._state()
            old = state["settings"]
            if settings["revision"] != old["revision"]:
                raise ValueError("Settings changed in another session. Reload before saving.")
            referenced = {record["printer_id"] for record in state["records"].values()}
            if referenced - {printer["id"] for printer in settings["printers"]}:
                raise ValueError("Printers with maintenance history must be archived, not removed.")
            self._check_destination_changes(old, settings, state["jobs"].values())
            if old == settings:
                return deepcopy(old)
            settings["revision"] += 1
            self._commit(state, {"type": "settings", "settings": settings})
            return deepcopy(settings)

    @staticmethod
    def _check_destination_changes(old, new, jobs):
        pending = [job for job in jobs if job["status"] in {"pending", "failed"}]
        for job in pending:
            if job["kind"] == "sheets":
                keys = ("auth_mode", "account_email", "spreadsheet_id", "folder_id")
                if any(old["google"][key] != new["google"][key] for key in keys):
                    raise ValueError("Deliver outstanding Sheets jobs before changing the Google destination.")
            elif job["kind"] == "email":
                keys = ("host", "port", "security", "username", "sender", "recipients")
                if any(old["email"][key] != new["email"][key] for key in keys):
                    raise ValueError("Deliver outstanding email jobs before changing their destination.")
            elif job["kind"] == "discord":
                destination_id = job["destination"]["id"]
                if destination_id not in {item["id"] for item in new["discord"]["destinations"]}:
                    raise ValueError("Deliver outstanding Discord jobs before removing their destination.")

    def _secrets(self):
        path = self.data_dir / "secrets.json"
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise RuntimeError("The credential store is unreadable.") from None
        if not isinstance(value, dict) or any(not isinstance(item, str) for item in value.values()):
            raise RuntimeError("The credential store is invalid.")
        return value

    def get_secret(self, name):
        _text(name, "Credential name", True, 200)
        with self.store.locked():
            return self._secrets().get(name)

    def all_secrets(self):
        with self.store.locked():
            return self._secrets()

    @staticmethod
    def _same_credential_destination(name, previous, replacement):
        """Credential rotation must repair retries without silently rerouting them."""
        if not replacement:
            return False
        if name == "smtp_password":
            return True
        if name.startswith("discord:"):
            before, after = urlparse(previous), urlparse(replacement)
            def destination(parsed):
                return (
                    parsed.scheme, parsed.netloc, parsed.path.rstrip("/").rsplit("/", 1)[0],
                    parse_qs(parsed.query).get("thread_id"),
                )
            return destination(before) == destination(after)
        try:
            before, after = json.loads(previous), json.loads(replacement)
            if name == "google_service_account":
                identity = "client_email"
            else:
                identity = "client_id"
                if name == "google_oauth_client":
                    before = before.get("installed", before.get("web", before))
                    after = after.get("installed", after.get("web", after))
            return bool(before.get(identity)) and before.get(identity) == after.get(identity)
        except (ValueError, TypeError, AttributeError):
            return False

    def set_secret(self, name, value):
        if not isinstance(name, str) or not (name in SECRET_NAMES or (name.startswith("discord:") and ID_PATTERN.fullmatch(name[8:]))):
            raise ValueError("Unknown credential name.")
        if not isinstance(value, str) or len(value) > 100000:
            raise ValueError("Credential must be text, at most 100000 characters.")
        if value and name.startswith("google_"):
            try:
                parsed = json.loads(value)
                if not isinstance(parsed, dict):
                    raise ValueError()
            except (ValueError, TypeError):
                raise ValueError("Google credential must contain a valid JSON object.") from None
        if value and name.startswith("discord:"):
            try:
                parsed = urlparse(value)
                port = parsed.port
            except ValueError:
                raise ValueError("Enter a Discord HTTPS webhook URL.") from None
            query = parse_qs(parsed.query, keep_blank_values=True)
            if (parsed.scheme != "https" or parsed.hostname not in {"discord.com", "discordapp.com", "canary.discord.com", "ptb.discord.com"}
                    or parsed.username or parsed.password or port not in (None, 443) or parsed.fragment
                    or not re.fullmatch(r"/api(?:/v\d+)?/webhooks/\d+/[A-Za-z0-9._-]+/?", parsed.path)
                    or any(key not in {"wait", "thread_id"} for key in query)
                    or ("thread_id" in query and (len(query["thread_id"]) != 1 or not query["thread_id"][0].isdigit()))):
                raise ValueError("Enter a Discord HTTPS webhook URL.")
        with self.store.locked():
            secrets = self._secrets()
            state = self._state()
            if secrets.get(name) == (value or None):
                return
            if secrets.get(name):
                for job in state["jobs"].values():
                    if job["status"] not in {"pending", "failed"}:
                        continue
                    relevant = (job["kind"] == "sheets" and name.startswith("google_")) or (job["kind"] == "email" and name == "smtp_password") or (job["kind"] == "discord" and name == "discord:" + job["destination"]["id"])
                    if relevant and not self._same_credential_destination(name, secrets[name], value):
                        raise ValueError("Deliver outstanding jobs before changing the credential's destination or disconnecting it. Credentials may be rotated for the same destination.")
            if value:
                secrets[name] = value
            else:
                secrets.pop(name, None)
            atomic_json(self.data_dir / "secrets.json", secrets)

    def secret_status(self):
        with self.store.locked():
            names = SECRET_NAMES | {"discord:" + item["id"] for item in self._state()["settings"]["discord"]["destinations"]}
            secrets = self._secrets()
            return {name: bool(secrets.get(name)) for name in sorted(names | set(secrets))}

    def records(self, kind=None):
        if kind is not None:
            _choice(kind, {"weekly", "reactive"}, "Record kind")
        with self.store.locked():
            values = self._state()["records"].values()
            return deepcopy(sorted((record for record in values if kind is None or record["kind"] == kind), key=lambda item: item["updated_at"], reverse=True))

    def activity(self):
        with self.store.locked():
            return deepcopy(self._state()["activity"])

    @staticmethod
    def _printer(settings, printer_id):
        _identifier(printer_id, "Printer ID")
        for printer in settings["printers"]:
            if printer["id"] == printer_id and printer["active"]:
                return printer
        raise ValueError("Choose an active printer.")

    @staticmethod
    def _revision(payload, previous):
        if previous and payload.get("revision") is not None and payload["revision"] != previous["revision"]:
            raise ValueError("This record changed in another session. Reload before saving.")

    @staticmethod
    def _job(kind, destination, payload):
        return {"id": str(uuid4()), "kind": kind, "status": "pending", "attempts": 0, "next_attempt_at": None, "created_at": now(), "destination": deepcopy(destination), "payload": deepcopy(payload), "last_error": ""}

    def _jobs_for(self, settings, event):
        jobs = []
        google = settings["google"]
        if google["enabled"] and google["spreadsheet_id"]:
            jobs.append(self._job("sheets", google, event))
        email = settings["email"]
        if email["enabled"] and event["event_type"] in email["events"]:
            jobs.append(self._job("email", email, event))
        if settings["discord"]["enabled"]:
            for destination in settings["discord"]["destinations"]:
                if destination["enabled"] and event["event_type"] in destination["events"] and (not destination["printer_ids"] or event["record"]["printer_id"] in destination["printer_ids"]):
                    jobs.append(self._job("discord", destination, event))
        return jobs

    def _save_record(self, state, record, event_type, previous):
        if previous:
            ignored = {"revision", "updated_at"}
            if {key: value for key, value in record.items() if key not in ignored} == {key: value for key, value in previous.items() if key not in ignored}:
                return deepcopy(previous)
        event = {"change_id": str(uuid4()), "event_type": event_type, "occurred_at": record["updated_at"], "actor": record.get("technician", ""), "record": record}
        self._commit(state, {"type": "record", "event": event, "jobs": self._jobs_for(state["settings"], event)})
        return deepcopy(record)

    def save_weekly(self, payload):
        allowed = {"id", "revision", "printer_id", "technician", "academic_year", "semester", "week", "checklist", "metrics", "note", "state"}
        _keys(payload, allowed, "Weekly record", ("printer_id",))
        _record_identity(payload)
        with self.store.locked():
            state = self._state()
            settings = state["settings"]
            printer = self._printer(settings, payload["printer_id"])
            schedule = settings["schedule"]
            key = (payload["printer_id"], payload.get("academic_year", schedule["academic_year"]), payload.get("semester", schedule["semester"]), payload.get("week", schedule["week"]))
            _text(key[1], "Academic year", True, 40)
            _number(key[2], "Semester", 1, 4, True)
            _number(key[3], "Week", 1, 53, True)
            previous = None
            if payload.get("id"):
                previous = state["records"].get(payload["id"])
                if not previous or previous["kind"] != "weekly":
                    raise ValueError("Weekly record was not found.")
                old_key = tuple(previous[name] for name in ("printer_id", "academic_year", "semester", "week"))
                if old_key != key:
                    raise ValueError("The printer and week of an existing weekly job cannot change.")
            else:
                previous = next((record for record in state["records"].values() if record["kind"] == "weekly" and tuple(record[name] for name in ("printer_id", "academic_year", "semester", "week")) == key), None)
            self._revision(payload, previous)
            timestamp = now()
            record = deepcopy(previous) if previous else {"id": str(uuid4()), "kind": "weekly", "created_at": timestamp, "settings_revision": settings["revision"], "checklist_definitions": deepcopy(settings["checklist"]), "metric_definitions": deepcopy(settings["metrics"])}
            record.update({name: value for name, value in payload.items() if name not in {"id", "revision"}})
            record.update(dict(zip(("printer_id", "academic_year", "semester", "week"), key)))
            record.update({"printer_name": printer["name"], "revision": (previous["revision"] if previous else 0) + 1, "updated_at": timestamp})
            record.setdefault("state", "draft")
            _choice(record["state"], {"draft", "completed"}, "Weekly state")
            record["technician"] = _text(record.get("technician", ""), "Technician", record["state"] == "completed", 200)
            record["note"] = _text(record.get("note", ""), "Notes")
            record.setdefault("checklist", {})
            record.setdefault("metrics", {})
            definitions = {item["id"]: item for item in record["checklist_definitions"]}
            metric_definitions = {item["id"]: item for item in record["metric_definitions"]}
            _keys(record["checklist"], definitions, "Checklist")
            _keys(record["metrics"], metric_definitions, "Usage readings")
            for value in record["checklist"].values():
                _bool(value, "Checklist answer")
            for value in record["metrics"].values():
                _number(value, "Cumulative usage reading")
            if record["state"] == "completed":
                missing = [item["label"] for item in definitions.values() if item["active"] and item["required"] and not record["checklist"].get(item["id"])]
                if missing:
                    raise ValueError("Complete required checklist items: " + ", ".join(missing))
                record["completed_at"] = (previous or {}).get("completed_at") or timestamp
            else:
                record["completed_at"] = None
            event_type = "weekly_completed" if record["state"] == "completed" and (previous or {}).get("state") != "completed" else "weekly_saved"
            return self._save_record(state, record, event_type, previous)

    def _incident(self, payload, state):
        settings = state["settings"]
        printer = self._printer(settings, payload["printer_id"])
        previous = None
        if payload.get("id"):
            previous = state["records"].get(payload["id"])
            if not previous or previous["kind"] != "reactive" or previous["printer_id"] != printer["id"]:
                raise ValueError("Incident was not found for this printer.")
        else:
            previous = next((record for record in state["records"].values() if record["kind"] == "reactive" and record["printer_id"] == printer["id"] and record["state"] in ACTIVE_INCIDENTS), None)
        self._revision(payload, previous)
        timestamp = now()
        record = deepcopy(previous) if previous else {"id": str(uuid4()), "kind": "reactive", "created_at": timestamp, "opened_at": timestamp, "settings_revision": settings["revision"], "source": "kiosk", "state": "open", "availability": "unavailable"}
        record.update({name: value for name, value in payload.items() if name not in {"id", "revision"}})
        record.update({"printer_name": printer["name"], "revision": (previous["revision"] if previous else 0) + 1, "updated_at": timestamp})
        _choice(record["state"], ACTIVE_INCIDENTS | {"resolved", "closed_unresolved"}, "Incident state")
        if record["state"] in ACTIVE_INCIDENTS and any(item["kind"] == "reactive" and item["printer_id"] == printer["id"] and item["id"] != record["id"] and item["state"] in ACTIVE_INCIDENTS for item in state["records"].values()):
            raise ValueError("Another open incident already exists for this printer.")
        if record["state"] == "resolved" and "availability" not in payload:
            record["availability"] = "available"
        _choice(record["availability"], {"available", "unavailable", "under_maintenance"}, "Printer availability")
        for name in ("technician", "source", "symptom", "summary", "action_taken", "component", "result_status", "fix_summary", "note"):
            record[name] = _text(record.get(name, ""), name.replace("_", " ").capitalize(), record["state"] == "resolved" and name in {"technician", "fix_summary"}, 10000 if name in {"note", "summary", "action_taken", "symptom"} else 1000)
        for name in ("diagnosis_path", "sop_ids"):
            record.setdefault(name, [])
            _list(record[name], name, 1000)
            for item in record[name]:
                _identifier(item, name)
        record["resolved_at"] = ((previous or {}).get("resolved_at") or timestamp) if record["state"] == "resolved" else None
        record["closed_at"] = ((previous or {}).get("closed_at") or timestamp) if record["state"] in {"resolved", "closed_unresolved"} else None
        event_type = "fault_opened" if previous is None else "incident_updated"
        if previous and previous["state"] != record["state"]:
            if record["state"] == "resolved":
                event_type = "incident_resolved"
            elif record["state"] == "escalated":
                event_type = "incident_escalated"
        return self._save_record(state, record, event_type, previous)

    def save_incident(self, payload):
        allowed = {"id", "revision", "printer_id", "technician", "source", "symptom", "summary", "action_taken", "component", "state", "result_status", "fix_summary", "availability", "note", "diagnosis_path", "sop_ids"}
        _keys(payload, allowed, "Incident", ("printer_id",))
        _record_identity(payload)
        with self.store.locked():
            return self._incident(payload, self._state())

    def trigger(self, printer_id, source="gpio_button"):
        _text(source, "Trigger source", True, 100)
        with self.store.locked():
            state = self._state()
            self._printer(state["settings"], printer_id)
            for record in state["records"].values():
                if record["kind"] == "reactive" and record["printer_id"] == printer_id and record["state"] in ACTIVE_INCIDENTS:
                    return deepcopy(record)
            return self._incident({"printer_id": printer_id, "source": source, "symptom": "Marked unavailable; awaiting diagnosis"}, state)

    def printer_summaries(self):
        with self.store.locked():
            state = self._state()
            result = []
            for printer in state["settings"]["printers"]:
                records = [record for record in state["records"].values() if record["printer_id"] == printer["id"]]
                incidents = sorted((record for record in records if record["kind"] == "reactive"), key=lambda record: record["updated_at"], reverse=True)
                incident = next((record for record in incidents if record["state"] in ACTIVE_INCIDENTS), None)
                completed = [record["completed_at"] for record in records if record["kind"] == "weekly" and record["completed_at"]]
                fixed = next((record for record in incidents if record["state"] == "resolved"), None)
                availability = (incident or (incidents[0] if incidents else {})).get("availability", "available")
                result.append({**printer, "printer_id": printer["id"], "printer_name": printer["name"], "availability": availability, "status": availability, "open_incident_id": incident["id"] if incident else None, "last_weekly_at": max(completed) if completed else None, "last_reactive_at": incidents[0]["updated_at"] if incidents else None, "latest_fix_summary": fixed["fix_summary"] if fixed else "", "current_fault_summary": (incident.get("summary") or incident.get("symptom")) if incident else ""})
            return result

    def jobs(self):
        with self.store.locked():
            return deepcopy(list(self._state()["jobs"].values()))

    def pending_jobs(self):
        return [job for job in self.jobs() if job["status"] in {"pending", "failed"}]

    def queue_job(self, kind, destination, payload):
        _choice(kind, {"sheets", "email", "discord"}, "Delivery kind")
        if not isinstance(destination, dict) or not isinstance(payload, dict) or not payload.get("change_id"):
            raise ValueError("Delivery needs a destination and an event change ID.")
        _identifier(payload["change_id"], "Change ID")
        # This internal helper must never persist credential values supplied by a caller.
        allowed = set(DEFAULT_SETTINGS["google"] if kind == "sheets" else DEFAULT_SETTINGS["email"] if kind == "email" else DEFAULT_SETTINGS["discord"]["destinations"][0])
        _keys(destination, allowed, "Delivery destination")
        with self.store.locked():
            state = self._state()
            for existing in state["jobs"].values():
                if existing["kind"] == kind and existing["destination"] == destination and existing["payload"]["change_id"] == payload["change_id"]:
                    return deepcopy(existing)
            job = self._job(kind, destination, payload)
            self._commit(state, {"type": "job", "job": job})
            return deepcopy(job)

    def update_job(self, job_id, **updates):
        _identifier(job_id, "Delivery ID")
        allowed = {"status", "attempts", "next_attempt_at", "last_attempt_at", "last_error", "external_id", "message_id", "sent_at", "result", "retryable"}
        _keys(updates, allowed, "Delivery update")
        if "status" in updates:
            _choice(updates["status"], {"pending", "failed", "sent"}, "Delivery status")
        if "attempts" in updates:
            _number(updates["attempts"], "Delivery attempts", 0, 10**9, True)
        if "retryable" in updates:
            _bool(updates["retryable"], "Delivery retryable")
        for key in ("last_error", "external_id", "message_id"):
            if key in updates:
                _text(updates[key], key)
        if "result" in updates and not isinstance(updates["result"], dict):
            raise ValueError("Delivery result must be an object.")
        for key in ("next_attempt_at", "last_attempt_at", "sent_at"):
            if key in updates and updates[key] is not None:
                _text(updates[key], key, True, 100)
                try:
                    parsed = datetime.fromisoformat(updates[key].replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        raise ValueError()
                except ValueError:
                    raise ValueError(f"{key} must be an ISO timestamp with timezone or null.") from None
        with self.store.locked():
            state = self._state()
            if job_id not in state["jobs"]:
                raise ValueError("Delivery job not found.")
            job = deepcopy(state["jobs"][job_id])
            job.update(updates)
            self._commit(state, {"type": "job", "job": job})
            return deepcopy(job)

    def get_diagnosis(self):
        with self.store.locked():
            graph = self._state()["diagnosis"]
            if graph is not None:
                return deepcopy(graph)
            try:
                return json.loads(self.diagnosis_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                raise ValueError("The initial diagnosis file is missing or invalid.") from None

    def save_diagnosis(self, graph):
        validate_diagnosis(graph)
        with self.store.locked():
            state = self._state()
            self._commit(state, {"type": "diagnosis", "diagnosis": deepcopy(graph)})
            return deepcopy(graph)

    def export_data(self):
        with self.store.locked():
            state = self._state()
            return deepcopy({"format_version": 1, "exported_at": now(), "settings": state["settings"], "records": list(state["records"].values()), "activity": state["activity"]})
