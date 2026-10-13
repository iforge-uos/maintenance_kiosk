"""Run the web kiosk, delivery worker, or Raspberry Pi hardware watcher."""
import argparse
import os
import time

from . import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", choices=["serve", "worker", "hardware"], default="serve")
    parser.add_argument("--host", default=os.environ.get("KIOSK_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("KIOSK_PORT", "5000")))
    parser.add_argument("--once", action="store_true", help="Process one delivery batch and exit.")
    args = parser.parse_args()
    app = create_app()
    service = app.extensions["kiosk"]
    if args.command == "serve":
        app.run(host=args.host, port=args.port, debug=False, use_reloader=False, threaded=True)
    elif args.command == "hardware":
        from .hardware import HardwareController
        HardwareController(service).run()
    else:
        from .worker import SyncWorker
        worker = SyncWorker(service, app.config["DATA_DIR"])
        while True:
            result = worker.run_once(backfill=True)
            if args.once:
                print(result)
                return
            time.sleep(max(5, int(service.get_settings()["google"].get("sync_interval", 30))))


if __name__ == "__main__":
    main()
