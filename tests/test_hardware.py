from copy import deepcopy
from types import SimpleNamespace

import pytest

from kiosk.domain import KioskService
from kiosk import hardware
from kiosk.hardware import HardwareController, _HoldDetector


@pytest.fixture
def service(tmp_path):
    return KioskService(tmp_path / "live")


def real_backend(service):
    settings = service.get_settings()
    settings["hardware"].update(backend="gpiozero", hold_seconds=0.1, debounce_seconds=0.02)
    service.save_settings(settings)


def test_hold_requires_debounce_and_stable_release():
    detector = _HoldDetector(1, 0.1)
    assert not detector.sample(True, 0)
    assert not detector.sample(False, 0.05)  # bounce never becomes a press
    assert not detector.sample(False, 0.2)
    assert not detector.sample(True, 0.3)
    assert not detector.sample(True, 0.5)
    assert detector.sample(True, 1.31)
    assert not detector.sample(True, 3)
    assert not detector.sample(False, 3.01)
    assert not detector.sample(True, 3.05)  # release bounce must not re-arm
    assert not detector.sample(True, 5)
    assert not detector.sample(False, 5.1)
    assert not detector.sample(False, 5.21)
    assert not detector.sample(True, 6)
    assert detector.sample(True, 7.01)


def test_simulation_keeps_production_records_jobs_and_settings_unchanged(service):
    before = deepcopy(service.export_data())
    before.pop("exported_at", None)
    controller = HardwareController(service)
    short = controller.simulate("core-one-01", 0.5)
    held = controller.simulate("core-one-01", 2)
    assert short["status"] == held["status"] == "passed"
    assert not short["details"]["triggered"]
    assert held["details"]["triggered"]
    assert held["details"]["test_record_id"]
    assert not held["details"]["physical_input_verified"]
    after = service.export_data()
    after.pop("exported_at", None)
    assert after == before
    assert service.jobs() == []


@pytest.mark.parametrize("duration", [-1, float("nan"), float("inf"), "wrong"])
def test_simulation_rejects_invalid_hold_values(service, duration):
    with pytest.raises(ValueError):
        HardwareController(service).simulate("core-one-01", duration)


def test_mock_mode_never_claims_real_hardware_pass(service):
    controller = HardwareController(service)
    assert controller.monitor("core-one-01")["status"] == "unavailable"
    assert controller.test_led("core-one-01")["status"] == "unavailable"
    assert not controller.status()["physical_available"]


def test_real_gpio_is_unavailable_off_pi(monkeypatch):
    monkeypatch.setattr(hardware, "_pi_model", lambda: None)
    with pytest.raises(hardware.HardwareUnavailable, match="Raspberry Pi"):
        hardware._gpio_classes()


def test_mock_pin_factory_cannot_pass_physical_test(monkeypatch):
    monkeypatch.setattr(hardware, "_pi_model", lambda: "Raspberry Pi")
    monkeypatch.setenv("GPIOZERO_PIN_FACTORY", "mock")
    with pytest.raises(hardware.HardwareUnavailable, match="mock pin factory"):
        hardware._gpio_classes()


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def time(self):
        return 1000 + self.now

    def sleep(self, seconds):
        self.now += seconds


def test_physical_monitor_uses_real_input_path_without_live_record(monkeypatch, service):
    real_backend(service)
    clock = Clock()
    buttons = []

    class Button:
        def __init__(self, *args, **kwargs):
            self.closed = False
            buttons.append(self)

        @property
        def is_pressed(self):
            return 0.03 <= clock.now < 0.25

        def close(self):
            self.closed = True

    monkeypatch.setattr(hardware, "time", clock)
    monkeypatch.setattr(hardware, "_gpio_classes", lambda: (Button, None))
    result = HardwareController(service).monitor("core-one-01", duration=0.4)
    assert result["status"] == "passed"
    assert len(result["details"]["hold_seconds"]) == 1
    assert result["details"]["debounced_transitions"] == 2
    assert buttons[0].closed
    assert service.records() == []
    assert not (service.data_dir / "gpio-test-lease.json").exists()


def test_led_restored_and_closed_even_when_test_interrupted(monkeypatch, service):
    real_backend(service)
    leds = []

    class LED:
        def __init__(self, pin, active_high, initial_value):
            self.value = initial_value
            self.initial = initial_value
            self.closed = False
            leds.append(self)

        def close(self):
            self.closed = True

    def interrupt(seconds):
        raise RuntimeError("interrupted")

    monkeypatch.setattr(hardware, "_gpio_classes", lambda: (None, LED))
    clock = Clock()
    monkeypatch.setattr(hardware, "time", SimpleNamespace(monotonic=clock.monotonic, time=clock.time, sleep=interrupt))
    with pytest.raises(RuntimeError, match="interrupted"):
        HardwareController(service).test_led("core-one-01")
    assert leds[0].value == leds[0].initial
    assert leds[0].closed
    assert not (service.data_dir / "gpio-test-lease.json").exists()


def test_watcher_emits_once_per_hold_and_reuses_open_incident(monkeypatch, service):
    real_backend(service)
    clock = Clock()
    controllers = []
    calls = []
    original_trigger = service.trigger

    def trigger(*args, **kwargs):
        calls.append((args, kwargs))
        return original_trigger(*args, **kwargs)

    class Button:
        def __init__(self, *args, **kwargs):
            self.closed = False
            controllers.append(self)

        @property
        def is_pressed(self):
            return 0.02 <= clock.now < 0.3 or 0.4 <= clock.now < 0.7

        def close(self):
            self.closed = True

    class LED:
        def __init__(self, *args, **kwargs):
            self.closed = False
            self.value = kwargs["initial_value"]
            controllers.append(self)

        def close(self):
            self.closed = True

    class Stop:
        def clear(self):
            pass

        def is_set(self):
            return clock.now >= 0.8

        def wait(self, duration):
            clock.sleep(duration)

    monkeypatch.setattr(service, "trigger", trigger)
    monkeypatch.setattr(hardware, "time", clock)
    monkeypatch.setattr(hardware, "_gpio_classes", lambda: (Button, LED))
    controller = HardwareController(service)
    controller._stop = Stop()
    controller.run()
    assert len(calls) == 2
    assert all(kwargs["source"] == "gpio_button" for _, kwargs in calls)
    assert len(service.records()) == 1
    assert all(device.closed for device in controllers)


def test_test_session_pauses_and_restores_watcher_lease(service):
    controller = HardwareController(service)
    assert not controller._paused()
    with controller._test_session(1):
        assert controller._paused()
        with pytest.raises(hardware.HardwareUnavailable, match="already running"):
            with HardwareController(service)._test_session(1):
                pass
    assert not controller._paused()
