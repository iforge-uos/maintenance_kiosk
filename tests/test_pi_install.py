"""Exercise Pi deployment safeguards without changing the host OS or services."""
from __future__ import annotations

import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from kiosk.pi_hardware import ConfiguredHardwareRunner


ROOT = Path(__file__).resolve().parents[1]


def git(directory, *args):
    return subprocess.run(
        ["git", "-C", str(directory), *args], check=True, capture_output=True
    ).stdout


@pytest.fixture
def checkout(tmp_path):
    """Use a committed disposable checkout, including the new deploy assets."""
    target = tmp_path / "checkout"
    target.mkdir()
    paths = git(ROOT, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    for value in paths.split(b"\0"):
        if not value:
            continue
        relative = Path(os.fsdecode(value))
        source = ROOT / relative
        if source.is_file():
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    git(target, "init", "-q")
    git(target, "add", ".")
    git(target, "-c", "user.name=Installer test", "-c", "user.email=test@example.invalid",
        "commit", "-qm", "Disposable installer test checkout")
    return target


@pytest.fixture
def guarded_environment(tmp_path):
    """Any attempt to elevate, install, or modify a service fails visibly."""
    if not shutil.which("getent"):
        pytest.skip("Installer account checks require the Linux getent utility")
    commands = tmp_path / "fake-bin"
    commands.mkdir()
    mutation_log = tmp_path / "mutations.log"
    for name in ("sudo", "apt", "apt-get", "systemctl", "install", "chown", "usermod",
                 "raspi-config", "reboot", "ln", "mv", "cp", "rm", "mkdir"):
        command = commands / name
        command.write_text(
            '#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "$KIOSK_TEST_MUTATIONS"\nexit 98\n'
        )
        command.chmod(0o755)
    # Keep the read-only systemctl query safe if this suite runs on a booted Pi.
    systemctl = commands / "systemctl"
    systemctl.write_text(
        '#!/bin/sh\nif [ "$1" = is-enabled ]; then printf "disabled\\n"; exit 1; fi\n'
        'printf "%s\\n" "$0 $*" >> "$KIOSK_TEST_MUTATIONS"\nexit 98\n'
    )
    # A regular test account lets --check reach repository guards even in root CI.
    # These mocks cannot change users or grant privileges: runuser only runs git.
    mocks = {
        "id": '#!/bin/sh\nif [ "$1" = -un ]; then printf "kiosk_test\\n"; else '
              f'exec {shlex.quote(shutil.which("id"))} "$@"; fi\n',
        "getent": '#!/bin/sh\nif [ "$1" = passwd ] && [ "$2" = kiosk_test ]; then\n'
                  f'printf "%s\\n" {shlex.quote(f"kiosk_test:x:1000:1000::{tmp_path}:/bin/bash")}\n'
                  f'else exec {shlex.quote(shutil.which("getent"))} "$@"; fi\n',
        "runuser": '#!/bin/sh\n[ "$1" = -u ] && [ "$2" = kiosk_test ] && '
                   '[ "$3" = -- ] && [ "$4" = git ] || exit 97\nshift 3\nexec "$@"\n',
    }
    for name, contents in mocks.items():
        command = commands / name
        command.write_text(contents)
        command.chmod(0o755)
    environment = os.environ.copy()
    environment["PATH"] = str(commands) + os.pathsep + environment.get("PATH", "")
    environment["KIOSK_TEST_MUTATIONS"] = str(mutation_log)
    return environment, mutation_log


def working_files(directory):
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(directory).parts
    }


def invoke(checkout, guarded_environment, *arguments):
    environment, mutation_log = guarded_environment
    before = working_files(checkout)
    result = subprocess.run(
        ["bash", str(checkout / "scripts/install-pi.sh"), *arguments],
        cwd=checkout,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert not mutation_log.exists(), mutation_log.read_text() if mutation_log.exists() else ""
    assert working_files(checkout) == before
    return result, result.stdout + result.stderr


def test_installer_help_is_read_only_and_works_off_pi(checkout, guarded_environment):
    result, output = invoke(checkout, guarded_environment, "--help")
    assert result.returncode == 0, output
    assert "--check" in output
    assert "--user" in output


def test_installer_check_refuses_root_kiosk_account_without_mutating_host(checkout, guarded_environment):
    result, output = invoke(checkout, guarded_environment, "--check", "--user", "root")
    assert result.returncode != 0
    assert "root" in output.lower()


@pytest.mark.skipif(platform.machine() in {"armv7l", "armv8l", "aarch64"}, reason="Requires a non-ARM host")
@pytest.mark.parametrize("mode", [[], ["--check"]])
def test_installer_refuses_non_pi_without_installing_anything(checkout, guarded_environment, mode):
    result, output = invoke(checkout, guarded_environment, *mode, "--user", "kiosk_test")
    assert result.returncode != 0
    assert "ARM Raspberry Pi" in output
    assert "No changes were made" in output


def test_installer_check_refuses_uncommitted_tracked_changes(checkout, guarded_environment):
    with (checkout / "README.md").open("a") as handle:
        handle.write("\nLocal notes that must be preserved.\n")
    result, output = invoke(checkout, guarded_environment, "--check", "--user", "kiosk_test")
    assert result.returncode != 0
    assert "tracked changes" in output
    assert "No changes were made" in output


def test_installer_check_rejects_release_with_missing_required_asset(checkout, guarded_environment):
    git(checkout, "rm", "kiosk/pi_hardware.py")
    git(checkout, "-c", "user.name=Installer test", "-c", "user.email=test@example.invalid",
        "commit", "-qm", "Incomplete release")
    result, output = invoke(checkout, guarded_environment, "--check", "--user", "kiosk_test")
    assert result.returncode != 0
    assert "absent from the selected commit: kiosk/pi_hardware.py" in output
    assert "No changes were made" in output


def test_boot_hardware_service_waits_for_settings_then_releases_disabled_controller():
    settings = {"hardware": {"backend": "mock"}}
    service = SimpleNamespace(get_settings=lambda: settings)
    controllers = []
    runs = []

    class Controller:
        def __init__(self, configured_service):
            assert configured_service is service
            assert settings["hardware"]["backend"] == "gpiozero"
            controllers.append(self)

        def run(self):
            runs.append(self)
            # The existing hardware watcher returns after a Settings change.
            settings["hardware"]["backend"] = "mock"

    class Event:
        stopped = False
        waits = 0

        def is_set(self):
            return self.stopped

        def wait(self, seconds):
            self.waits += 1
            if self.waits == 1:
                # Booting with mock controls must not claim or open GPIO pins.
                assert controllers == []
                settings["hardware"]["backend"] = "gpiozero"
            elif self.waits == 3:
                self.stopped = True

    runner = ConfiguredHardwareRunner(service, controller_factory=Controller)
    runner.stopped = Event()
    runner.run()
    assert len(controllers) == len(runs) == 1
    assert runner.stopped.waits == 3
    assert runner.controller is None


def test_stopping_boot_hardware_service_stops_active_watcher():
    service = SimpleNamespace(get_settings=lambda: {"hardware": {"backend": "gpiozero"}})
    events = []

    class Controller:
        def __init__(self, service):
            pass

        def run(self):
            events.append("run")
            runner.stop()

        def stop(self):
            events.append("stop")

    runner = ConfiguredHardwareRunner(service, controller_factory=Controller)
    runner.run()
    assert events == ["run", "stop"]
    assert runner.stopped.is_set()
    assert runner.controller is None


def test_boot_hardware_service_clears_failed_controller_for_service_restart():
    service = SimpleNamespace(get_settings=lambda: {"hardware": {"backend": "gpiozero"}})

    class Controller:
        def __init__(self, service):
            pass

        def run(self):
            raise RuntimeError("GPIO already in use")

    runner = ConfiguredHardwareRunner(service, controller_factory=Controller)
    with pytest.raises(RuntimeError, match="GPIO already in use"):
        runner.run()
    assert runner.controller is None
