from kiosk.domain import KioskService
from kiosk.integrations import IntegrationError
from kiosk.worker import SyncWorker


def connected_service(tmp_path):
    service = KioskService(tmp_path)
    config = service.get_settings()
    config["google"].update(enabled=True, spreadsheet_id="test-spreadsheet-id")
    service.save_settings(config)
    return service


def test_worker_retries_without_losing_committed_record(tmp_path, monkeypatch):
    service = connected_service(tmp_path)
    incident = service.trigger("core-one-01")
    calls = []

    class FakeSheets:
        def __init__(self, settings, secrets):
            pass

        def sync(self, event, printers):
            calls.append(event["change_id"])
            if len(calls) == 1:
                raise IntegrationError("Temporary service outage", retryable=True, retry_after=60)
            return {"synced": True}

    monkeypatch.setattr("kiosk.worker.GoogleSheets", FakeSheets)
    worker = SyncWorker(service, tmp_path)
    first = worker.run_once(backfill=True)
    assert first["failed"] == 1
    assert first["pending"] == 1
    assert service.records()[0]["id"] == incident["id"]
    assert worker.run_once()["sent"] == 0  # Backoff is respected.
    second = worker.run_once(retry_failed=True)
    assert second["sent"] == 1
    assert second["pending"] == 0
    assert calls[0] == calls[1]
    assert worker.run_once(backfill=True)["sent"] == 0


def test_connecting_sheets_backfills_offline_history(tmp_path, monkeypatch):
    service = KioskService(tmp_path)
    service.trigger("core-one-01")
    assert service.jobs() == []
    settings = service.get_settings()
    settings["google"].update(enabled=True, spreadsheet_id="test-sheet")
    service.save_settings(settings)
    calls = []

    class FakeSheets:
        def __init__(self, settings, secrets):
            pass

        def sync(self, event, printers):
            calls.append(event["change_id"])
            return {"synced": True}

    monkeypatch.setattr("kiosk.worker.GoogleSheets", FakeSheets)
    worker = SyncWorker(service, tmp_path)
    assert worker.run_once(backfill=True)["sent"] == 1
    assert worker.run_once(backfill=True)["sent"] == 0
    assert len(calls) == 1


def test_destination_failure_does_not_block_other_channels(tmp_path, monkeypatch):
    service = connected_service(tmp_path)
    record = service.trigger("core-one-01")
    event = service.activity()[0]
    service.queue_job("discord", {"id": "maintenance", "name": "Maintenance"}, event)

    class FailingSheets:
        def __init__(self, settings, secrets):
            pass

        def sync(self, event, printers):
            raise IntegrationError("Permission denied", retryable=False)

    monkeypatch.setattr("kiosk.worker.GoogleSheets", FailingSheets)
    monkeypatch.setattr("kiosk.worker.send_discord", lambda *args, **kwargs: {"message_id": "123"})
    report = SyncWorker(service, tmp_path).run_once()
    assert report["failed"] == 1
    assert report["sent"] == 1
    assert service.records()[0]["id"] == record["id"]
