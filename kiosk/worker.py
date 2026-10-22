"""Serialized, restartable delivery of already committed local changes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import fcntl
from pathlib import Path

from .domain import technician_name
from .integrations import GoogleSheets, IntegrationError, send_discord, send_email


def now():
    return datetime.now(timezone.utc)


class SyncWorker:
    def __init__(self, service, data_dir):
        self.service = service
        self.data_dir = Path(data_dir)

    def backfill(self):
        settings = self.service.get_settings()
        destination = settings["google"]
        if not destination.get("enabled") or not destination.get("spreadsheet_id"):
            return
        known = {
            (job["payload"].get("change_id"), job["destination"].get("spreadsheet_id"))
            for job in self.service.jobs() if job["kind"] == "sheets"
        }
        for event in self.service.activity():
            if not event.get("record") or not event.get("change_id"):
                continue
            key = (event["change_id"], destination["spreadsheet_id"])
            if key not in known:
                self.service.queue_job("sheets", destination, event)
                known.add(key)

    def run_once(self, backfill=False, retry_failed=False):
        report = {"sent": 0, "failed": 0, "pending": 0, "busy": False, "results": []}
        lock_path = self.data_dir / ".delivery.lock"
        with lock_path.open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {**report, "busy": True, "message": "The delivery worker is already running."}
            if backfill:
                self.backfill()
            secrets = self.service.all_secrets()
            jobs = sorted(self.service.pending_jobs(), key=lambda job: (job["created_at"], job["id"]))
            # Preserve order for each workbook/channel even when a delivery fails.
            blocked_destinations = set()
            attempted = 0
            for job in jobs:
                if attempted >= 100:
                    break
                destination = job["destination"]
                route = (job["kind"], destination.get("spreadsheet_id") or destination.get("id") or destination.get("host", ""))
                if route in blocked_destinations:
                    continue
                if job.get("status") == "failed" and not job.get("retryable", True) and not retry_failed:
                    blocked_destinations.add(route)
                    continue
                next_attempt = job.get("next_attempt_at")
                if next_attempt and not retry_failed:
                    try:
                        if datetime.fromisoformat(next_attempt.replace("Z", "+00:00")) > now():
                            blocked_destinations.add(route)
                            continue
                    except (TypeError, ValueError):
                        pass
                attempts = job.get("attempts", 0) + 1
                attempted += 1
                try:
                    if job["kind"] == "sheets":
                        settings = self.service.get_settings()
                        settings["google"] = destination
                        result = GoogleSheets(settings, secrets).sync(job["payload"], self.service.printer_summaries())
                    elif job["kind"] == "email":
                        result = send_email(destination, secrets.get("smtp_password", ""), self.notification_payload(job))
                    elif job["kind"] == "discord":
                        result = send_discord(destination, secrets.get("discord:" + destination["id"], ""), self.notification_payload(job))
                    else:
                        raise IntegrationError("Unknown delivery type.", retryable=False)
                except IntegrationError as error:
                    retryable = getattr(error, "retryable", False)
                    delay = min(3600, 5 * (2 ** min(attempts, 9)))
                    if getattr(error, "retry_after", None):
                        delay = max(delay, float(error.retry_after))
                    message = str(error)
                    self.service.update_job(
                        job["id"], status="pending" if retryable else "failed", attempts=attempts,
                        retryable=retryable, last_error=message, last_attempt_at=now().isoformat(),
                        next_attempt_at=(now() + timedelta(seconds=delay)).isoformat() if retryable else None,
                    )
                    blocked_destinations.add(route)
                    report["failed"] += 1
                    report["results"].append({"id": job["id"], "kind": job["kind"], "status": "failed", "message": message})
                except Exception:
                    # Never expose HTTP URLs, email passwords, or provider exception bodies.
                    self.service.update_job(job["id"], status="failed", attempts=attempts, retryable=False, last_error="Unexpected delivery failure; check integration configuration.", next_attempt_at=None)
                    blocked_destinations.add(route)
                    report["failed"] += 1
                    report["results"].append({"id": job["id"], "kind": job["kind"], "status": "failed", "message": "Unexpected delivery failure; check integration configuration."})
                else:
                    self.service.update_job(job["id"], status="sent", attempts=attempts, last_error="", next_attempt_at=None, sent_at=now().isoformat(), result=result)
                    report["sent"] += 1
                    report["results"].append({"id": job["id"], "kind": job["kind"], "status": "sent"})
            report["pending"] = len(self.service.pending_jobs())
        return report

    def notification_payload(self, job):
        payload = dict(job["payload"])
        record_id = payload.get("record", {}).get("id")
        current = next((record for record in self.service.records() if record["id"] == record_id), None)
        if current:
            payload["current_state"] = current
        settings = self.service.get_settings()
        if "technician_name" not in payload:
            payload["technician_name"] = technician_name(settings, payload.get("record", {}).get("technician", ""))
        if settings["google"].get("spreadsheet_id"):
            payload["spreadsheet_id"] = settings["google"]["spreadsheet_id"]
        return payload
