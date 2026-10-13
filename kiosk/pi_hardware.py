"""Boot service: wait for physical controls to be enabled in Settings."""
from __future__ import annotations

import signal
import threading

from .hardware import HardwareController


class ConfiguredHardwareRunner:
    def __init__(self, service, *, controller_factory=HardwareController, poll_seconds=5):
        self.service = service
        self.controller_factory = controller_factory
        self.poll_seconds = poll_seconds
        self.stopped = threading.Event()
        self.controller = None

    def stop(self):
        self.stopped.set()
        if self.controller is not None:
            self.controller.stop()

    def run(self):
        while not self.stopped.is_set():
            if self.service.get_settings()["hardware"]["backend"] != "gpiozero":
                self.stopped.wait(self.poll_seconds)
                continue
            self.controller = self.controller_factory(self.service)
            try:
                if not self.stopped.is_set():
                    self.controller.run()
            finally:
                self.controller = None
            self.stopped.wait(self.poll_seconds)


def main():
    from . import create_app

    runner = ConfiguredHardwareRunner(create_app().extensions["kiosk"])
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: runner.stop())
    runner.run()


if __name__ == "__main__":
    main()
