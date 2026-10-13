"""Operator-invoked checks. Simulated records never enter the production journal."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse
import uuid

from .hardware import HardwareController, HardwareUnavailable


SUPPORTED_CHECKS = frozenset({"config", "diagnosis", "google", "google_write", "email", "discord",
                              "trigger", "physical", "led", "offline", "recovery"})


def _result(status, message, **details):
    result = {"status": status, "message": message}
    if details:
        result["details"] = details
    return result


def _redact(value, service):
    """Prevent even nested credential fields from leaking through adapter errors."""
    secrets = []

    def collect(item):
        if isinstance(item, str) and item:
            secrets.append(item)
            try:
                parsed = json.loads(item)
            except (ValueError, TypeError):
                return
            if isinstance(parsed, (dict, list)):
                collect(parsed)
        elif isinstance(item, dict):
            for child in item.values():
                collect(child)
        elif isinstance(item, list):
            for child in item:
                collect(child)

    collect(service.all_secrets())

    def clean(item):
        if isinstance(item, str):
            for secret in sorted(set(secrets), key=len, reverse=True):
                # Short nonsecret JSON fields (e.g. credential type) should not
                # destroy ordinary diagnostic prose; root secret values always mask.
                if len(secret) > 4 or secret in service.all_secrets().values():
                    item = item.replace(secret, "[redacted]")
            item = re.sub(r"https://(?:[a-z]+\.)?discord(?:app)?\.com/api/webhooks/[^\s\"']+", "[redacted webhook]", item)
            return item
        if isinstance(item, dict):
            return {key: clean(child) for key, child in item.items()}
        if isinstance(item, list):
            return [clean(child) for child in item]
        return item

    return clean(value)


def _isolated_settings(service, isolated):
    settings = deepcopy(service.get_settings())
    settings["revision"] = isolated.get_settings()["revision"]
    for name in ("google", "email", "discord"):
        settings[name]["enabled"] = False
    isolated.save_settings(settings)
    return settings


def _check_config(service):
    from .domain import KioskService

    with tempfile.TemporaryDirectory(prefix="kiosk-config-test-") as directory:
        isolated = KioskService(directory)
        settings = deepcopy(service.get_settings())
        settings["revision"] = isolated.get_settings()["revision"]
        isolated.save_settings(settings)
    return _result("passed", "Current settings passed validation; no messages were sent.",
                   printers=len(settings["printers"]), technicians=len(settings["technicians"]),
                   integrations_enabled={name: settings[name]["enabled"] for name in ("google", "email", "discord")},
                   hardware=HardwareController(service).status(), credentials_checked=False)


def _check_diagnosis(service):
    tree = service.get_diagnosis()
    nodes = tree.get("nodes", {})
    categories = tree.get("categories", {})
    sops = tree.get("sops", {})
    errors = []
    edges = {}
    if not isinstance(nodes, dict) or not nodes:
        return _result("failed", "The diagnosis tree has no usable nodes.")
    if not isinstance(categories, dict) or not categories:
        return _result("failed", "The diagnosis tree has no categories.")
    if not isinstance(sops, dict):
        return _result("failed", "SOP definitions must be an object keyed by SOP ID.")
    for node_id, node in nodes.items():
        if not isinstance(node, dict):
            errors.append(f"Node {node_id} is not an object.")
            edges[node_id] = []
            continue
        targets = []
        if node.get("type") == "question":
            if not node.get("text") or not node.get("options"):
                errors.append(f"Question {node_id} needs text and answer options.")
            for option in node.get("options", []):
                if not isinstance(option, dict) or not option.get("label"):
                    errors.append(f"Question {node_id} has an invalid answer option.")
                    continue
                target = option.get("next")
                if target not in nodes:
                    errors.append(f"Question {node_id} references a missing destination.")
                else:
                    targets.append(target)
        elif node.get("type") == "diagnosis":
            if not node.get("label") or not node.get("recommended_actions"):
                errors.append(f"Diagnosis {node_id} needs a label and recommended actions.")
            for sop_id in node.get("sop_ids", []):
                if sop_id not in sops:
                    errors.append(f"Diagnosis {node_id} references missing SOP {sop_id}.")
        else:
            errors.append(f"Node {node_id} has an unsupported type.")
        edges[node_id] = targets
    roots = []
    for category_id, category in categories.items():
        if not isinstance(category, dict) or not category.get("label") or category.get("start_node") not in nodes:
            errors.append(f"Category {category_id} needs a label and valid start node.")
        else:
            roots.append(category["start_node"])
    # Iterative DFS avoids recursion failures on user-authored long trees.
    colors = {}
    for root in nodes:
        if colors.get(root) == 2:
            continue
        stack = [(root, False)]
        while stack:
            node_id, exiting = stack.pop()
            if exiting:
                colors[node_id] = 2
                continue
            if colors.get(node_id) == 1:
                errors.append(f"Cycle detected at {node_id}.")
                continue
            if colors.get(node_id) == 2:
                continue
            colors[node_id] = 1
            stack.append((node_id, True))
            stack.extend((target, False) for target in edges[node_id])
    reachable = set()
    pending = list(roots)
    while pending:
        node_id = pending.pop()
        if node_id not in reachable:
            reachable.add(node_id)
            pending.extend(edges[node_id])
    unreachable = sorted(set(nodes) - reachable)
    if unreachable:
        errors.append(f"{len(unreachable)} diagnosis nodes cannot be reached from a category.")
    missing_documents = []
    external_documents = []
    repository_root = Path(__file__).resolve().parent.parent
    for sop_id, sop in sops.items():
        if not isinstance(sop, dict) or not sop.get("title"):
            errors.append(f"SOP {sop_id} needs a title.")
            continue
        location = sop.get("url") or sop.get("file", "")
        parsed = urlparse(location)
        if parsed.scheme == "https" and parsed.netloc:
            external_documents.append(sop_id)
        elif location.startswith("/sops/") or location.startswith("sops/"):
            relative = location.lstrip("/")
            candidates = [repository_root / relative, Path(service.data_dir) / relative]
            # A link cannot make diagnostics probe arbitrary filesystem paths.
            safe = [path for path in candidates if ".." not in Path(relative).parts]
            if not any(path.is_file() for path in safe):
                missing_documents.append(sop_id)
        else:
            missing_documents.append(sop_id)
    if errors or missing_documents:
        return _result("failed", "Diagnosis or SOP checks found items requiring attention.", errors=errors,
                       missing_documents=missing_documents, external_documents_unverified=external_documents,
                       unreachable_nodes=unreachable, node_count=len(nodes))
    if external_documents:
        return _result("unavailable", "Diagnosis graph is valid; remote SOP availability has not been checked.",
                       external_documents_unverified=external_documents, node_count=len(nodes))
    return _result("passed", "Diagnosis references, reachability, content and local SOP files are valid.", node_count=len(nodes), sop_count=len(sops))


# Monkeypatching an adapter in the web process could affect a simultaneous real
# sync. Run the fake adapter in its own interpreter, against only a temp store.
_QUEUE_WORKER_SCRIPT = r'''
import json
import sys
from unittest.mock import patch
from kiosk.domain import KioskService
from kiosk.integrations import IntegrationError
from kiosk.worker import SyncWorker

service = KioskService(sys.argv[1])
mode = sys.argv[2]
deliveries = {}
offline = mode in {"offline", "offline_then_online"}

class FakeSheets:
    def __init__(self, settings, secrets):
        if settings["google"]["spreadsheet_id"] != "diagnostic-local-only":
            raise RuntimeError("Diagnostic destination mismatch")
    def sync(self, event, printers):
        if offline:
            raise IntegrationError("Simulated offline adapter", retryable=True)
        deliveries[event["change_id"]] = event
        return {"synced": True, "simulation": True}

worker = SyncWorker(service, service.data_dir)
with patch("kiosk.worker.GoogleSheets", FakeSheets):
    first = worker.run_once(retry_failed=True)
    if mode == "offline_then_online":
        offline = False
        final = worker.run_once(retry_failed=True)
    else:
        final = first
print(json.dumps({"first": first, "final": final, "deliveries": len(deliveries),
                  "record_ids": [record["id"] for record in service.records()]}))
'''


def _queue_worker(directory, mode):
    completed = subprocess.run(
        [sys.executable, "-c", _QUEUE_WORKER_SCRIPT, str(directory), mode],
        cwd=Path(__file__).resolve().parent.parent,
        check=True, capture_output=True, text=True, timeout=15,
    )
    return json.loads(completed.stdout)


def _queue_check(service, recovery=False):
    from .domain import KioskService

    with tempfile.TemporaryDirectory(prefix="kiosk-queue-test-") as directory:
        isolated = KioskService(directory)
        settings = _isolated_settings(service, isolated)
        printer = next((p for p in settings["printers"] if p.get("active", True)), None)
        if printer is None:
            return _result("unavailable", "Add an active printer before running a queue test.")
        record = isolated.trigger(printer["id"], source="offline_diagnostic")
        event = {"change_id": str(uuid.uuid4()), "record": record, "event_type": "fault_opened"}
        destination = deepcopy(settings["google"])
        destination["spreadsheet_id"] = "diagnostic-local-only"
        job = isolated.queue_job("sheets", destination, event)
        original_id = record["id"]
        if recovery:
            first = _queue_worker(directory, "offline")
            if first["final"]["failed"] != 1 or first["final"]["pending"] != 1:
                return _result("failed", "The isolated worker did not retain the simulated failed delivery.")
            pending = KioskService(directory).pending_jobs()
            if len(pending) != 1 or pending[0]["id"] != job["id"]:
                return _result("failed", "The isolated pending job did not survive its worker process exiting.")
            delivered = _queue_worker(directory, "online")
        else:
            delivered = _queue_worker(directory, "offline_then_online")
            if delivered["first"]["failed"] != 1 or delivered["first"]["pending"] != 1:
                return _result("failed", "The isolated worker did not retain the simulated failed delivery.")
        isolated = KioskService(directory)
        jobs = isolated.jobs()
        verified = (not isolated.pending_jobs() and len(jobs) == 1 and jobs[0]["status"] == "sent" and
                    jobs[0]["attempts"] == 2 and delivered["deliveries"] == 1 and
                    delivered["record_ids"] == [original_id] and delivered["final"]["sent"] == 1)
        return _result("passed" if verified else "failed",
                       "A fresh worker process recovered the isolated pending job and delivered it to a local fake adapter." if recovery
                       else "The isolated delivery worker retained a record during a simulated outage and completed its retry.",
                       simulation=True, reopened_store=True, process_restart_tested=recovery,
                       full_app_restart_tested=False,
                       external_services_tested=False, production_records_changed=False,
                       test_record_id=original_id, test_job_id=job["id"], deliveries=delivered["deliveries"])


def _run(name, service, options):
    if name == "config":
        return _check_config(service)
    if name == "diagnosis":
        return _check_diagnosis(service)
    if name in {"offline", "recovery"}:
        return _queue_check(service, recovery=name == "recovery")
    if name in {"trigger", "physical", "led"}:
        hardware = HardwareController(service)
        printer_id = options.get("printer_id")
        if not printer_id:
            raise ValueError("Choose a printer for this hardware test.")
        if name == "trigger":
            return hardware.simulate(printer_id, options.get("hold_seconds", service.get_settings()["hardware"]["hold_seconds"]))
        if name == "physical":
            return hardware.monitor(printer_id, options.get("duration", 5))
        return hardware.test_led(printer_id)

    from .integrations import GoogleSheets, send_discord, send_email

    settings = service.get_settings()
    secrets = service.all_secrets()
    if name in {"google", "google_write"}:
        if not settings["google"].get("spreadsheet_id"):
            return _result("unavailable", "Choose a Google Sheets workbook in Settings first.")
        credential = "google_oauth_token" if settings["google"].get("auth_mode") == "oauth" else "google_service_account"
        if not secrets.get(credential):
            return _result("unavailable", "Connect the Google account or supply its service-account credentials in Settings first.")
        adapter = GoogleSheets(settings, secrets)
        details = adapter.check() if name == "google" else adapter.test_write()
        if name == "google_write":
            if not details.get("verified") or not details.get("cleaned_up"):
                return _result("failed", "The isolated Sheets write/read test or its cleanup did not complete.", **details)
            return _result("passed", "A dedicated Sheets test row was written, verified and removed.", **details)
        if details.get("schema_issues"):
            return _result("failed", "Workbook access succeeded but its column schema needs attention.", **details)
        if not details.get("connected"):
            return _result("failed", "Google workbook access was not confirmed.", **details)
        if details.get("owner_verified") is not True:
            return _result("unavailable", "Workbook access succeeded, but ownership by the configured Google account could not be verified. Check Drive permissions before syncing.", **details)
        if settings["google"].get("folder_id") and details.get("folder_verified") is not True:
            return _result("unavailable", "Workbook access succeeded, but its configured Drive folder could not be verified. Check the folder selection and Drive permissions.", **details)
        return _result("passed", "Google account and workbook access verified; no records were written.", **details)
    payload = {"event_type": "diagnostic_test", "change_id": f"test-{uuid.uuid4().hex}",
               "record": {"id": "TEST-ONLY", "printer_id": "TEST-ONLY", "summary": "Operator-requested maintenance kiosk test. This is not a real fault.",
                          "state": "test", "created_at": datetime.now(timezone.utc).isoformat()}}
    if name == "email":
        recipient = options.get("recipient", "")
        if not isinstance(recipient, str) or not re.fullmatch(r"[^\s@<>;,]+@[^\s@<>;,]+\.[^\s@<>;,]+", recipient):
            raise ValueError("Enter one valid recipient address for the test email.")
        config = deepcopy(settings["email"])
        if not config.get("host") or not config.get("sender"):
            return _result("unavailable", "Configure the email host and sender before testing.")
        config["recipients"] = [recipient]
        details = send_email(config, secrets.get("smtp_password", ""), payload, test=True)
        accepted = bool(details.get("accepted"))
        details = {**details, "recipient": recipient, "delivery_confirmed": False}
        return _result("passed" if accepted else "failed", "Test email accepted by the mail server; delivery to the inbox is not confirmed." if accepted else "The mail server did not accept the test email.",
                       **details)
    if name == "discord":
        destination_id = options.get("destination_id")
        config = next((item for item in settings["discord"]["destinations"] if item["id"] == destination_id), None)
        if config is None:
            raise ValueError("Choose a configured Discord destination.")
        webhook = secrets.get(f"discord:{destination_id}")
        if not webhook:
            return _result("unavailable", "Save the webhook URL for this destination before testing.", destination_id=destination_id)
        details = send_discord(config, webhook, payload, test=True)
        accepted = bool(details.get("accepted"))
        return _result("passed" if accepted else "failed", "Discord accepted the labelled test message." if accepted else "Discord did not confirm the test message.",
                       destination_id=destination_id, **details)
    raise ValueError("Unknown diagnostic check.")


def run_check(name, service, options=None):
    """Run one explicit check; config and diagnosis never send or write remotely."""
    started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        if name not in SUPPORTED_CHECKS:
            raise ValueError("Unknown diagnostic check.")
        if options is not None and not isinstance(options, dict):
            raise ValueError("Diagnostic options must be an object.")
        result = _run(name, service, options or {})
    except HardwareUnavailable as exc:
        result = _result("unavailable", str(exc))
    except ValueError as exc:
        result = _result("failed", str(exc))
    except Exception as exc:
        # Only the integration's intentional, sanitized messages are exposed.
        from .integrations import IntegrationError
        if isinstance(exc, IntegrationError):
            result = _result("failed", str(exc), retryable=exc.retryable)
        else:
            result = _result("failed", f"The check could not finish ({type(exc).__name__}); verify configuration and device access.")
    result["check"] = name
    result["started_at"] = started_at
    result["duration_ms"] = round((time.monotonic() - started) * 1000)
    return _redact(result, service)
