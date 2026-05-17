from __future__ import annotations

import argparse

import backend


def run_sync(backfill: bool = False, limit: int | None = None) -> int:
    if backfill:
        backend.queue_google_sheets_backfill()
    return backend.process_pending_sheet_syncs(limit=limit)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync maintenance kiosk data to Google Sheets via Apps Script.")
    parser.add_argument("--backfill", action="store_true", help="Queue existing Postgres data before syncing.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum sync queue rows to process.")
    args = parser.parse_args()
    sent_count = run_sync(backfill=args.backfill, limit=args.limit)
    print(f"Google Sheets sync rows sent: {sent_count}")


if __name__ == "__main__":
    main()
