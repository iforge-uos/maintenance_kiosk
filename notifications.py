from __future__ import annotations

import json
import os
import smtplib
import urllib.request
from email.message import EmailMessage
from typing import Any, Callable

import backend


def send_email_job(job: dict[str, Any]) -> None:
    backend.load_env_file()
    message = EmailMessage()
    message["Subject"] = job["subject"] or "Maintenance kiosk notification"
    message["From"] = os.environ["SMTP_FROM_EMAIL"]
    message["To"] = job["recipient_email"]
    message.set_content(job["body"] or "")

    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ["SMTP_USERNAME"]
    password = os.environ["SMTP_PASSWORD"]

    with smtplib.SMTP(host, port, timeout=20) as smtp:
        smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(message)


def send_google_chat_job(job: dict[str, Any]) -> None:
    backend.load_env_file()
    message = {"text": job["body"] or ""}
    relay_secret = os.environ.get("GOOGLE_CHAT_WEBHOOK_SECRET", "").strip()
    if relay_secret:
        message["secret"] = relay_secret

    payload = json.dumps(message).encode("utf-8")
    request = urllib.request.Request(
        job["target_url"],
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        response_body = response.read().decode("utf-8")
        status = response.status

    if status < 200 or status >= 300:
        raise RuntimeError(f"Google Chat webhook returned HTTP {status}: {response_body}")
    if not response_body.strip():
        return

    try:
        parsed = json.loads(response_body)
    except json.JSONDecodeError:
        return
    if parsed.get("ok") is False or parsed.get("success") is False:
        raise RuntimeError(f"Google Chat webhook rejected message: {response_body}")


def process_pending_notifications(
    send_email: Callable[[dict[str, Any]], None] = send_email_job,
    send_google_chat: Callable[[dict[str, Any]], None] = send_google_chat_job,
) -> dict[str, int]:
    result = {"sent": 0, "failed": 0}
    for job in backend.pending_notification_jobs():
        try:
            if job["channel"] == "email":
                send_email(job)
            elif job["channel"] == "google_chat":
                send_google_chat(job)
            else:
                raise ValueError(f"Unknown notification channel: {job['channel']}")
        except Exception as exc:
            backend.mark_notification_failed(job["notification_id"], str(exc))
            result["failed"] += 1
        else:
            backend.mark_notification_sent(job["notification_id"])
            result["sent"] += 1
    return result
