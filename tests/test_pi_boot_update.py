"""Exercise boot updates with disposable Git repositories and no host services."""
from __future__ import annotations

import importlib.util
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("pi_boot_update", ROOT / "scripts/update-pi.py")
boot_update = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(boot_update)


def git(directory, *arguments):
    return subprocess.run(
        ["git", "-C", str(directory), *arguments],
        check=True, capture_output=True,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    ).stdout


def commit(directory, message="Test change"):
    git(directory, "add", ".")
    git(directory, "-c", "user.name=Boot update test", "-c",
        "user.email=test@example.invalid", "commit", "-qm", message)
    return git(directory, "rev-parse", "HEAD").decode().strip()


@pytest.fixture
def installation(tmp_path, monkeypatch):
    """Use real fetch/merge/archive, substituting only privileged OS boundaries."""
    author = tmp_path / "author"
    author.mkdir()
    git(author, "init", "-q", "-b", "kiosk-test")
    (author / "app.txt").write_text("Installed application\n")
    (author / "requirements.txt").write_text("dependency==1\n")
    (author / "requirements-pi.lock").write_text("locked dependency\n")
    initial = commit(author, "Installed version")
    remote = tmp_path / "remote.git"
    git(tmp_path, "clone", "--bare", "-q", str(author), str(remote))
    git(author, "remote", "add", "origin", str(remote))
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", "-q", str(remote), str(checkout))
    base = tmp_path / "installed"
    release = base / "releases" / initial
    release.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(git(author, "archive", initial))) as archive:
        # This fixture archive only contains the three ordinary files above.
        for member in archive.getmembers():
            (release / member.name).write_bytes(archive.extractfile(member).read())
    (release / ".source-commit").write_text(initial + "\n")
    runtime = release / ".venv"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin/python").write_text("Fixture runtime, never executed\n")
    (base / "current").symlink_to(release)
    data = tmp_path / "data"
    data.mkdir()
    (data / "maintenance-journal").write_text("Existing maintenance record\n")
    (data / "settings.json").write_text('{"discord": "private configuration"}\n')
    account = SimpleNamespace(pw_uid=os.getuid(), pw_dir=str(tmp_path), pw_name="testuser")
    monkeypatch.setattr(boot_update.pwd, "getpwnam", lambda name: account)
    # Test paths are writable temporary directories, not /opt or /etc.
    monkeypatch.setattr(boot_update, "root_managed", lambda path: None)

    class DisposableUpdater(boot_update.Updater):
        states = {}

        def __init__(self):
            super().__init__({"checkout": str(checkout), "user": "testuser", "branch": "kiosk-test"}, base)
            self.validated = []
            self.commands = []

        def run(self, command, **kwargs):
            self.commands.append(tuple(map(str, command)))
            if command[0] == "systemctl":
                assert command[1:4] == ["show", "--property=ActiveState", "--value"]
                return self.states.get(command[-1], "inactive").encode()
            # Do not elevate or change account. All commands use the test account
            # against its own disposable files; preserve the production runner.
            kwargs["user"] = False
            return super().run(command, **kwargs)

        def git(self, *arguments, **kwargs):
            if arguments == ("remote", "get-url", "origin"):
                return b"https://github.com/iforge-uos/maintenance_kiosk.git\n"
            return super().git(*arguments, **kwargs)

        def validate_release(self, path):
            self.validated.append(Path(path))
            assert (path / "app.txt").is_file()
            assert (path / ".venv").resolve() == runtime

    updater = DisposableUpdater()
    fixture = SimpleNamespace(updater=updater, author=author, remote=remote, checkout=checkout,
                              base=base, release=release, runtime=runtime, initial=initial, data=data)

    def publish(path="app.txt", contents="Updated application\n"):
        destination = author / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(contents)
        target = commit(author)
        git(author, "push", "-q", "origin", "kiosk-test")
        return target

    fixture.publish = publish
    return fixture


def snapshot(directory):
    return {str(path.relative_to(directory)): path.read_bytes()
            for path in directory.rglob("*") if path.is_file()}


def assert_retained(installation):
    assert (installation.base / "current").resolve() == installation.release
    assert (installation.release / "app.txt").read_text() == "Installed application\n"


def test_update_fetches_fast_forward_and_preserves_data_untracked_files_and_runtime(installation):
    item = installation
    before = snapshot(item.data)
    runtime_before = snapshot(item.runtime)
    (item.checkout / "local-notes.txt").write_text("Keep these notes\n")
    target = item.publish()

    assert item.updater.update() is True

    new_release = item.base / "releases" / target
    assert (item.base / "current").resolve() == new_release
    assert (new_release / ".source-commit").read_text().strip() == target
    assert (new_release / "app.txt").read_text() == "Updated application\n"
    assert (new_release / ".venv").is_symlink()
    assert (new_release / ".venv").resolve() == item.runtime
    assert git(item.checkout, "rev-parse", "HEAD").decode().strip() == target
    assert (item.checkout / "local-notes.txt").read_text() == "Keep these notes\n"
    assert not (new_release / "local-notes.txt").exists()
    assert snapshot(item.data) == before
    assert snapshot(item.runtime) == runtime_before
    assert item.updater.validated[-1] == new_release
    assert item.updater.validated == [new_release]
    assert not list((item.base / "releases").glob(".boot-*"))
    assert not list(item.base.glob(".boot-current-*"))


def test_unchanged_remote_does_not_stage_or_validate_another_release(installation, capsys):
    item = installation
    assert item.updater.update() is False
    assert_retained(item)
    assert item.updater.validated == []
    assert list((item.base / "releases").iterdir()) == [item.release]
    assert "Kiosk is current" in capsys.readouterr().out


def test_unavailable_remote_exhausts_bounded_retry_without_changing_release(installation, monkeypatch):
    item = installation
    git(item.checkout, "remote", "set-url", "origin", str(item.remote / "missing"))
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(boot_update, "time", SimpleNamespace(
        monotonic=lambda: clock.now, sleep=lambda seconds: setattr(clock, "now", clock.now + seconds)))
    item.updater.deadline = clock.now + 140
    with pytest.raises(boot_update.UpdateError, match="unavailable or authentication failed"):
        item.updater.update()
    assert clock.now <= 1050
    assert_retained(item)
    assert git(item.checkout, "rev-parse", "HEAD").decode().strip() == item.initial
    assert item.updater.validated == []


@pytest.mark.parametrize("change", ["dirty", "branch", "diverged", "rewritten"])
def test_local_edits_other_branch_and_non_fast_forward_history_are_retained(installation, change):
    item = installation
    item.publish()
    if change == "dirty":
        (item.checkout / "app.txt").write_text("My unsaved code\n")
        message = "local tracked edits"
    elif change == "branch":
        git(item.checkout, "switch", "-qc", "another-branch")
        message = "another branch"
    elif change == "diverged":
        (item.checkout / "local.txt").write_text("My local commit\n")
        commit(item.checkout, "Retain local work")
        message = "diverged or was rewritten"
    else:
        git(item.author, "switch", "--orphan", "replacement")
        (item.author / "app.txt").write_text("Rewritten history\n")
        commit(item.author, "Replacement root commit")
        git(item.author, "push", "-q", "--force", "origin", "HEAD:refs/heads/kiosk-test")
        message = "diverged or was rewritten"
    local_head = git(item.checkout, "rev-parse", "HEAD")
    app_before = (item.checkout / "app.txt").read_bytes()
    with pytest.raises(boot_update.UpdateError, match=message):
        item.updater.update()
    assert_retained(item)
    assert git(item.checkout, "rev-parse", "HEAD") == local_head
    assert (item.checkout / "app.txt").read_bytes() == app_before


def test_untracked_file_collision_stops_update_without_deleting_local_file(installation):
    item = installation
    (item.checkout / "new-page.txt").write_text("My private draft\n")
    item.publish("new-page.txt", "Published page\n")
    with pytest.raises(boot_update.UpdateError):
        item.updater.update()
    assert_retained(item)
    assert (item.checkout / "new-page.txt").read_text() == "My private draft\n"
    assert git(item.checkout, "rev-parse", "HEAD").decode().strip() == item.initial


def test_ignored_private_file_collision_stops_update_without_overwriting_file(installation):
    item = installation
    with (item.checkout / ".git/info/exclude").open("a") as output:
        output.write("\nprivate-data.txt\n")
    (item.checkout / "private-data.txt").write_text("Existing private data\n")
    assert git(item.checkout, "check-ignore", "private-data.txt").strip() == b"private-data.txt"
    item.publish("private-data.txt", "New repository file\n")
    with pytest.raises(boot_update.UpdateError):
        item.updater.update()
    assert_retained(item)
    assert (item.checkout / "private-data.txt").read_text() == "Existing private data\n"
    assert git(item.checkout, "rev-parse", "HEAD").decode().strip() == item.initial


@pytest.mark.parametrize("path", ["requirements.txt", "requirements-pi.lock", "deploy/new.service",
                                 "scripts/update-pi.py", "scripts/install-pi.sh"])
def test_changed_dependency_or_deployment_contract_stops_before_checkout_merge(installation, path):
    item = installation
    item.publish(path, "A change that needs a manual install\n")
    with pytest.raises(boot_update.UpdateError, match="Dependencies or Pi setup files changed"):
        item.updater.update()
    assert_retained(item)
    assert git(item.checkout, "rev-parse", "HEAD").decode().strip() == item.initial
    assert item.updater.validated == []


def test_failed_release_validation_keeps_installed_release_and_data(installation):
    item = installation
    target = item.publish()
    before = snapshot(item.data)
    calls = []

    def reject(path):
        calls.append(path)
        raise boot_update.UpdateError("Isolated kiosk check failed")

    item.updater.validate_release = reject
    with pytest.raises(boot_update.UpdateError, match="Isolated kiosk check failed"):
        item.updater.update()
    assert_retained(item)
    assert snapshot(item.data) == before
    assert calls == [item.base / "releases" / target]
    assert not list((item.base / "releases").glob(".boot-*"))


def test_real_broken_app_import_fails_smoke_and_does_not_publish(installation):
    item = installation
    item.publish("kiosk/__init__.py", "raise RuntimeError('Broken release fixture')\n")

    def validate(path):
        item.updater.run([sys.executable, "-I", "-c", boot_update.SMOKE, str(path)],
                         label="Isolated kiosk check")

    item.updater.validate_release = validate
    with pytest.raises(boot_update.UpdateError, match="Isolated kiosk check failed"):
        item.updater.update()
    assert_retained(item)


@pytest.mark.parametrize("service", boot_update.SERVICES)
def test_running_service_refuses_update_before_fetch(installation, service):
    item = installation
    item.updater.states = {service + ".service": "active"}
    item.publish()
    with pytest.raises(boot_update.UpdateError, match="service is running"):
        item.updater.update()
    assert_retained(item)
    assert not (item.checkout / ".git/FETCH_HEAD").exists()


def test_service_that_starts_during_validation_prevents_publish(installation):
    item = installation
    item.publish()

    def validate(path):
        item.updater.states = {"lightdm.service": "activating"}

    item.updater.validate_release = validate
    with pytest.raises(boot_update.UpdateError, match="service is running"):
        item.updater.update()
    assert_retained(item)


def test_failed_atomic_publication_retains_current_and_cleans_temporary_link(installation, monkeypatch):
    item = installation
    item.publish()

    def reject_replace(source, destination):
        raise OSError("Simulated filesystem error")

    monkeypatch.setattr(boot_update.os, "replace", reject_replace)
    with pytest.raises(OSError, match="filesystem error"):
        item.updater.update()
    assert_retained(item)
    assert not list(item.base.glob(".boot-current-*"))


def test_interrupted_prepared_release_is_checked_again_before_use(installation):
    item = installation
    target = item.publish()
    validate = item.updater.validate_release
    item.updater.validate_release = lambda path: None
    publish = item.updater.publish
    item.updater.publish = lambda path: (_ for _ in ()).throw(boot_update.UpdateError("Power failure"))
    with pytest.raises(boot_update.UpdateError, match="Power failure"):
        item.updater.update()
    assert_retained(item)
    item.updater.publish = publish
    item.updater.validate_release = validate
    assert item.updater.update() is True
    assert item.updater.validated == [item.base / "releases" / target]


@pytest.mark.parametrize("name,kind", [
    ("../escape", "file"), ("/absolute", "file"), ("safe/../../escape", "file"),
    (".venv/bin/python", "file"), ("./.venv", "directory"), (".source-commit", "file"),
    (".git/config", "file"), ("linked", "symlink"), ("hard-linked", "hardlink"),
    ("pipe", "fifo"),
])
def test_archive_rejects_links_traversal_and_installer_private_paths_before_extracting(installation, tmp_path, name, kind):
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w") as archive:
        ordinary = tarfile.TarInfo("ordinary.txt")
        ordinary.size = 4
        archive.addfile(ordinary, io.BytesIO(b"safe"))
        unsafe = tarfile.TarInfo(name)
        unsafe.type = {"file": tarfile.REGTYPE, "directory": tarfile.DIRTYPE,
                       "symlink": tarfile.SYMTYPE, "hardlink": tarfile.LNKTYPE,
                       "fifo": tarfile.FIFOTYPE}[kind]
        if kind in {"symlink", "hardlink"}:
            unsafe.linkname = "../outside"
        archive.addfile(unsafe)
    destination = tmp_path / "extract"
    destination.mkdir()
    with pytest.raises(boot_update.UpdateError, match="unsafe archive entry"):
        installation.updater.extract(payload.getvalue(), destination)
    assert list(destination.iterdir()) == []


def test_isolated_smoke_check_runs_real_app_without_touching_configured_data(tmp_path):
    live = tmp_path / "configured-data"
    live.mkdir()
    (live / "sentinel").write_text("Must stay unchanged\n")
    before = snapshot(live)
    result = subprocess.run(
        [sys.executable, "-I", "-c", boot_update.SMOKE, str(ROOT)],
        cwd=tmp_path, env={**os.environ, "KIOSK_DATA_DIR": str(live), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert snapshot(live) == before
    assert not (tmp_path / "instance").exists()


@pytest.mark.skipif(not Path("/proc").exists(), reason="Checks Linux process state")
def test_runner_timeout_kills_child_processes(installation, tmp_path):
    pid_path = tmp_path / "child-pid"
    child = "import time; time.sleep(60)"
    parent = (
        "import subprocess, sys, time; from pathlib import Path; "
        f"p = subprocess.Popen([sys.executable, '-c', {child!r}]); "
        f"Path({str(pid_path)!r}).write_text(str(p.pid)); time.sleep(60)"
    )
    started = time.monotonic()
    with pytest.raises(boot_update.UpdateError, match="timed out"):
        installation.updater.run([sys.executable, "-c", parent], timeout=0.5, label="Child test")
    assert time.monotonic() - started < 5
    assert pid_path.exists()
    child_pid = int(pid_path.read_text())
    status = Path(f"/proc/{child_pid}/stat")
    try:
        # An orphan can briefly remain a zombie until the host reaps it.
        assert not status.exists() or status.read_text().split()[2] == "Z"
    finally:
        if status.exists() and status.read_text().split()[2] != "Z":
            os.kill(child_pid, signal.SIGKILL)


@pytest.mark.parametrize("arguments,expected", [([], 0), (["--check"], 1)])
def test_boot_entry_point_allows_kiosk_start_but_reports_configuration_check_failure(monkeypatch, capsys, arguments, expected):
    monkeypatch.setattr(sys, "argv", ["update-pi.py", *arguments])
    monkeypatch.setattr(boot_update.os, "geteuid", lambda: 0)
    # main sets the deployment umask; this unit test must not change pytest's.
    monkeypatch.setattr(boot_update.os, "umask", lambda value: 0o022)

    def missing_configuration():
        raise OSError("Configuration file is missing")

    monkeypatch.setattr(boot_update, "load_config", missing_configuration)
    assert boot_update.main() == expected
    output = capsys.readouterr().out
    assert "Update skipped" in output
    assert "Starting the installed kiosk version" in output


@pytest.mark.parametrize("arguments,expected", [(["--help"], 0), (["--user", "root"], 1), (["--invalid"], 1)])
def test_enable_script_help_and_argument_guards_are_read_only(tmp_path, arguments, expected):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    mutations = tmp_path / "mutations"
    for name in ("sudo", "systemctl", "install", "flock", "chown", "reboot", "apt-get", "git"):
        command = fake_bin / name
        command.write_text('#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "$TEST_MUTATIONS"\nexit 98\n')
        command.chmod(0o755)
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/enable-pi-boot-update.sh"), *arguments],
        cwd=tmp_path, env={**os.environ, "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
                           "TEST_MUTATIONS": str(mutations)},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    assert not mutations.exists()
    if expected == 0:
        assert "--branch" in result.stdout
        assert "Records and settings remain" in result.stdout
