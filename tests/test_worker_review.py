"""Regression coverage for independent delivery destinations under backlog."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from kiosk.domain import KioskService
from kiosk.worker import SyncWorker


@pytest.mark.parametrize("permanent_failure", [True, False])
def test_blocked_sheets_backlog_does_not_starve_later_discord(
    tmp_path, monkeypatch, permanent_failure
):
    service = KioskService(tmp_path)
    service.trigger("core-one-01")
    event = service.activity()[0]
    destination = service.get_settings()["google"]
    destination["spreadsheet_id"] = "review-test-workbook"
    first_job = None
    for index in range(100):
        change = deepcopy(event)
        change["change_id"] = f"backlog-change-{index}"
        job = service.queue_job("sheets", destination, change)
        if first_job is None:
            first_job = job
    if permanent_failure:
        service.update_job(first_job["id"], status="failed", retryable=False)
    else:
        service.update_job(
            first_job["id"],
            next_attempt_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        )
    discord = service.queue_job(
        "discord", service.get_settings()["discord"]["destinations"][0], event
    )
    deliveries = []

    def unexpected_sheets(*args, **kwargs):
        pytest.fail("The blocked Sheets route must remain paused.")

    def accept_discord(config, secret, payload):
        deliveries.append(payload["change_id"])
        return {"accepted": True, "message_id": "test-message-id"}

    monkeypatch.setattr("kiosk.worker.GoogleSheets", unexpected_sheets)
    monkeypatch.setattr("kiosk.worker.send_discord", accept_discord)

    report = SyncWorker(service, tmp_path).run_once()

    assert deliveries == [event["change_id"]]
    assert report["sent"] == 1
    assert report["pending"] == 100
    assert next(job for job in service.jobs() if job["id"] == discord["id"])["status"] == "sent"
