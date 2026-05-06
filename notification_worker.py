from __future__ import annotations

import notifications


def main() -> None:
    result = notifications.process_pending_notifications()
    print(f"Sent {result['sent']} notification(s), failed {result['failed']} notification(s).")


if __name__ == "__main__":
    main()
