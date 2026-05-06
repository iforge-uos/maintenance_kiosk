from __future__ import annotations

import os

import backend


def main() -> None:
    kiosk_base_url = os.environ.get("KIOSK_BASE_URL", "http://127.0.0.1:5050")
    queued = backend.queue_weekly_reminder_emails(kiosk_base_url=kiosk_base_url)
    print(f"Queued {queued} weekly reminder email(s).")


if __name__ == "__main__":
    main()
