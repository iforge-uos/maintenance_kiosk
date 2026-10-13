"""GPIO input and output with a deliberately separate diagnostic record store.

gpiozero is imported only for an explicit hardware operation on a Raspberry Pi.
BCM pin numbers are used throughout. A short-lived lease pauses the production
watcher while a diagnostic owns the pins, including across web/worker processes.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import fcntl
import importlib.util
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid


class HardwareUnavailable(RuntimeError):
    """No real GPIO hardware can be safely used for this operation."""


class _HoldDetector:
    """Debounce transitions and emit at most one hold until a stable release."""

    def __init__(self, hold_seconds, debounce_seconds):
        self.hold = float(hold_seconds)
        self.debounce = float(debounce_seconds)
        self.raw = False
        self.stable = False
        self.changed_at = 0.0
        self.pressed_at = None
        self.fired = False
        self.transitions = 0

    def sample(self, pressed, now):
        if bool(pressed) != self.raw:
            self.raw = bool(pressed)
            self.changed_at = now
        if self.raw != self.stable and now - self.changed_at >= self.debounce:
            self.stable = self.raw
            self.transitions += 1
            if self.stable:
                self.pressed_at = self.changed_at
            else:
                self.pressed_at = None
                self.fired = False
        if (self.stable and self.raw and not self.fired and
                self.pressed_at is not None and now - self.pressed_at >= self.hold):
            self.fired = True
            return True
        return False


def _positive_number(value, name, maximum=30, allow_zero=False):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number.") from None
    if not math.isfinite(result) or result < 0 or (result == 0 and not allow_zero) or result > maximum:
        raise ValueError(f"{name} must be {'0' if allow_zero else 'greater than 0'} to {maximum} seconds.")
    return result


def _pi_model():
    for filename in ("/proc/device-tree/model", "/sys/firmware/devicetree/base/model"):
        try:
            model = Path(filename).read_text().rstrip("\x00").strip()
            if "raspberry pi" in model.lower():
                return model
        except OSError:
            pass
    return None


def _gpio_classes():
    if not _pi_model():
        raise HardwareUnavailable("Physical GPIO tests require a Raspberry Pi; use Simulated trigger in the cloud.")
    if os.environ.get("GPIOZERO_PIN_FACTORY", "").lower() == "mock":
        raise HardwareUnavailable("A mock pin factory cannot verify physical hardware.")
    try:
        from gpiozero import Button, Device, LED
    except ImportError:
        raise HardwareUnavailable("Install the Raspberry Pi hardware dependencies to enable GPIO.") from None
    if Device.pin_factory is not None and "mock" in type(Device.pin_factory).__name__.lower():
        raise HardwareUnavailable("A mock pin factory cannot verify physical hardware.")
    return Button, LED


class HardwareController:
    def __init__(self, service):
        self.service = service
        self._stop = threading.Event()
        self._directory = Path(service.data_dir)

    def status(self):
        settings = self.service.get_settings()["hardware"]
        board = _pi_model()
        return {
            "backend": settings["backend"],
            "board": board,
            "physical_available": bool(board and settings["backend"] == "gpiozero" and
                                       importlib.util.find_spec("gpiozero") is not None and
                                       os.environ.get("GPIOZERO_PIN_FACTORY", "").lower() != "mock"),
            "numbering": "BCM",
            "mappings": deepcopy(settings.get("mappings", [])),
            "simulation_available": True,
        }

    def _printer(self, printer_id):
        settings = self.service.get_settings()
        if not any(p["id"] == printer_id and p.get("active", True) for p in settings["printers"]):
            raise ValueError("Choose an active printer.")
        return settings

    def _mapping(self, printer_id):
        settings = self._printer(printer_id)
        if settings["hardware"]["backend"] != "gpiozero":
            raise HardwareUnavailable("Physical GPIO is unavailable in mock mode; select gpiozero on a Raspberry Pi.")
        mapping = next((m for m in settings["hardware"]["mappings"] if m["printer_id"] == printer_id), None)
        if mapping is None:
            raise HardwareUnavailable("No GPIO mapping is configured for this printer.")
        return settings["hardware"], mapping

    def simulate(self, printer_id, hold_seconds):
        """Exercise the normal handler in a temporary store; never notify or log live."""
        from .domain import KioskService

        settings = deepcopy(self._printer(printer_id))
        held = _positive_number(hold_seconds, "Hold duration", 120, allow_zero=True)
        threshold = float(settings["hardware"]["hold_seconds"])
        settings["google"]["enabled"] = False
        settings["email"]["enabled"] = False
        settings["discord"]["enabled"] = False
        with tempfile.TemporaryDirectory(prefix="kiosk-trigger-test-") as directory:
            isolated = KioskService(directory)
            settings["revision"] = isolated.get_settings()["revision"]
            isolated.save_settings(settings)
            # Deliberately do not claim to simulate electrical debounce.
            triggered = held >= threshold
            record = isolated.trigger(printer_id, source="simulated_test") if triggered else None
            return {
                "status": "passed",
                "message": "Simulated hold created an isolated test incident." if triggered else "Short simulated press was correctly ignored.",
                "details": {"mode": "simulation", "printer_id": printer_id, "hold_seconds": held,
                            "required_hold_seconds": threshold, "triggered": triggered,
                            "test_record_id": record["id"] if record else None,
                            "production_records_changed": False, "notifications_sent": False,
                            "physical_input_verified": False},
            }

    @property
    def _pause_path(self):
        return self._directory / "gpio-test-lease.json"

    def _paused(self):
        try:
            lease = json.loads(self._pause_path.read_text())
            return float(lease["expires_at"]) > time.time()
        except (OSError, ValueError, KeyError, TypeError):
            return False

    @contextmanager
    def _lock(self, timeout=0):
        self._directory.mkdir(parents=True, exist_ok=True)
        with (self._directory / "gpio.lock").open("a+") as handle:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise HardwareUnavailable("GPIO is in use by another session. Try again after it finishes.") from None
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def _test_session(self, duration):
        # A second test cannot replace another test's pause lease.
        self._directory.mkdir(parents=True, exist_ok=True)
        with (self._directory / "gpio-diagnostics.lock").open("a+") as guard:
            try:
                fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise HardwareUnavailable("Another hardware test is already running.") from None
            token = uuid.uuid4().hex
            lease = {"token": token, "expires_at": time.time() + duration + 5}
            staging = self._directory / f"gpio-test-{token}.tmp"
            try:
                staging.write_text(json.dumps(lease))
                os.replace(staging, self._pause_path)
                with self._lock(timeout=3):
                    yield
            finally:
                self._pause_path.unlink(missing_ok=True)
                staging.unlink(missing_ok=True)
                fcntl.flock(guard.fileno(), fcntl.LOCK_UN)

    def _normal_led(self, printer_id):
        summary = next((p for p in self.service.printer_summaries() if p.get("id", p.get("printer_id")) == printer_id), {})
        return summary.get("availability", "available") != "available"

    def test_led(self, printer_id):
        try:
            _, mapping = self._mapping(printer_id)
            _, LED = _gpio_classes()
            if mapping.get("led_pin") is None:
                raise HardwareUnavailable("No LED pin is configured for this printer.")
            with self._test_session(1):
                led = LED(mapping["led_pin"], active_high=mapping.get("led_active_high", True), initial_value=self._normal_led(printer_id))
                previous = led.value
                try:
                    led.value = 1 - previous
                    time.sleep(0.5)
                finally:
                    # Restore before closing, even when toggling or sleeping failed.
                    try:
                        led.value = previous
                    finally:
                        led.close()
            return {"status": "passed", "message": "LED output was toggled and restored; confirm the light visually.",
                    "details": {"printer_id": printer_id, "pin": mapping["led_pin"], "numbering": "BCM",
                                "restored_value": previous, "visual_confirmation_required": True}}
        except HardwareUnavailable as exc:
            return {"status": "unavailable", "message": str(exc)}

    def monitor(self, printer_id, duration=5):
        duration = _positive_number(duration, "Monitoring duration", 60)
        try:
            settings, mapping = self._mapping(printer_id)
            Button, _ = _gpio_classes()
            if duration <= max(settings["hold_seconds"], settings["debounce_seconds"]):
                raise ValueError("Monitoring duration must exceed the configured hold and debounce times.")
            transitions = []
            holds = []
            detector = _HoldDetector(settings["hold_seconds"], settings["debounce_seconds"])
            with self._test_session(duration):
                button = Button(mapping["button_pin"], pull_up=mapping.get("pull_up", True), bounce_time=None)
                start = time.monotonic()
                previous = False
                try:
                    while time.monotonic() - start < duration:
                        now = time.monotonic()
                        pressed = bool(button.is_pressed)
                        if pressed != previous:
                            transitions.append({"elapsed_seconds": round(now - start, 3), "pressed": pressed})
                            previous = pressed
                        if detector.sample(pressed, now):
                            holds.append(round(now - detector.pressed_at, 3))
                        time.sleep(0.01)
                finally:
                    button.close()
            return {"status": "passed" if holds else "failed",
                    "message": "Physical button hold detected; no live incident was created." if holds else "No qualifying physical hold was observed during the test window.",
                    "details": {"mode": "physical", "printer_id": printer_id, "pin": mapping["button_pin"],
                                "duration_seconds": duration, "transitions": transitions,
                                "debounced_transitions": detector.transitions, "hold_seconds": holds,
                                "required_hold_seconds": settings["hold_seconds"],
                                "configured_debounce_seconds": settings["debounce_seconds"],
                                "production_records_changed": False}}
        except HardwareUnavailable as exc:
            return {"status": "unavailable", "message": str(exc)}

    def stop(self):
        self._stop.set()

    def run(self):
        """Poll real inputs until stopped; reload mappings when settings change.

        A hold generates one event per press. Only a stable release re-arms it.
        Devices close during diagnostics and reopen when the lease ends/expires.
        """
        if self.service.get_settings()["hardware"]["backend"] != "gpiozero":
            raise HardwareUnavailable("The production GPIO watcher requires the gpiozero backend on a Raspberry Pi.")
        Button, LED = _gpio_classes()
        self._stop.clear()
        while not self._stop.is_set():
            if self._paused():
                self._stop.wait(0.05)
                continue
            with self._lock(timeout=1):
                if self._paused():
                    continue
                hardware = self.service.get_settings()["hardware"]
                if hardware["backend"] != "gpiozero":
                    return
                devices = []
                try:
                    active = {p["id"] for p in self.service.get_settings()["printers"] if p.get("active", True)}
                    for mapping in hardware["mappings"]:
                        if mapping["printer_id"] not in active:
                            continue
                        button = Button(mapping["button_pin"], pull_up=mapping.get("pull_up", True), bounce_time=None)
                        entry = {"mapping": mapping, "button": button, "led": None,
                                 "detector": _HoldDetector(hardware["hold_seconds"], hardware["debounce_seconds"])}
                        devices.append(entry)
                        # A button already held while a diagnostic finishes (or
                        # the watcher starts) must be released before live logging.
                        if button.is_pressed:
                            entry["detector"].raw = entry["detector"].stable = True
                            entry["detector"].fired = True
                            entry["detector"].changed_at = time.monotonic()
                        if mapping.get("led_pin") is not None:
                            entry["led"] = LED(mapping["led_pin"], active_high=mapping.get("led_active_high", True),
                                               initial_value=self._normal_led(mapping["printer_id"]))
                    next_refresh = 0
                    while not self._stop.is_set() and not self._paused():
                        now = time.monotonic()
                        if now >= next_refresh:
                            latest = self.service.get_settings()
                            latest_active = {p["id"] for p in latest["printers"] if p.get("active", True)}
                            if latest["hardware"] != hardware or latest_active != active:
                                break
                            for entry in devices:
                                if entry["led"] is not None:
                                    entry["led"].value = self._normal_led(entry["mapping"]["printer_id"])
                            next_refresh = now + 0.5
                        for entry in devices:
                            if entry["detector"].sample(entry["button"].is_pressed, now):
                                self.service.trigger(entry["mapping"]["printer_id"], source="gpio_button")
                                if entry["led"] is not None:
                                    entry["led"].value = self._normal_led(entry["mapping"]["printer_id"])
                        self._stop.wait(0.01)
                finally:
                    for entry in devices:
                        entry["button"].close()
                        if entry["led"] is not None:
                            entry["led"].close()
