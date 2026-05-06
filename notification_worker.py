from __future__ import annotations

from app import initialize_database
import notifications


def main() -> None:
    initialize_database()
    result = notifications.process_pending_notifications()
    print(f"Sent {result['sent']} notification(s), failed {result['failed']} notification(s).")


if __name__ == "__main__":
    main()
