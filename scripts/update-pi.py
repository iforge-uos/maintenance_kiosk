#!/usr/bin/env python3
"""Check a configured Git branch before the installed kiosk starts at boot.

Only the final symlink replacement changes the installed application. No live
data, OS packages, service configuration, or user edits are changed here.
"""
from __future__ import annotations

import argparse
import fcntl
import io
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import shutil
import signal
import stat
import subprocess
import tarfile
import tempfile
import time


BASE = Path("/opt/maintenance-kiosk")
CONFIG = Path("/etc/maintenance-kiosk/boot-update.json")
LOCK = Path("/run/lock/maintenance-kiosk-deploy.lock")
SERVICES = ("maintenance-kiosk", "maintenance-kiosk-worker", "maintenance-kiosk-hardware", "lightdm")
# A manual install must apply changes to these files before boot updates resume.
CONTRACT = (
    "requirements.txt", "requirements-pi.lock", "deploy",
    "scripts/install-pi.sh", "scripts/enable-pi-boot-update.sh",
    "scripts/update-pi.py", "scripts/check-pi-runtime.py",
    "scripts/kiosk-browser.sh", "scripts/kiosk-session.sh",
)
SMOKE = """
import importlib, sys, tempfile
sys.path.insert(0, sys.argv[1])
from kiosk import create_app
for name in ('kiosk.worker', 'kiosk.pi_hardware'):
    importlib.import_module(name)
with tempfile.TemporaryDirectory(prefix='kiosk-update-check-') as data:
    app = create_app({'TESTING': True, 'DATA_DIR': data})
    client = app.test_client()
    health = client.get('/api/health')
    assert health.status_code == 200 and health.json['status'] == 'ok'
    for route in ('/', '/settings', '/testing'):
        assert client.get(route).status_code == 200, route
    assert not app.extensions['kiosk'].records()
"""


class UpdateError(Exception):
    """A failed update leaves the current installed version in place."""


def note(message):
    print(message, flush=True)


def root_managed(path):
    """Reject a user-replaceable configuration or installation path."""
    path = Path(path)
    for item in (path, *path.parents):
        details = item.lstat()
        if item.is_symlink() or details.st_uid != 0 or details.st_mode & 0o022:
            raise UpdateError(f"Expected a root-owned, protected path: {path}")


def load_config(path=CONFIG):
    root_managed(path)
    config = json.loads(path.read_text())
    if set(config) != {"checkout", "user", "branch"} or not all(isinstance(v, str) for v in config.values()):
        raise UpdateError("Invalid boot update configuration. Run the enable script again.")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*\$?", config["user"]):
        raise UpdateError("Invalid kiosk account.")
    account = pwd.getpwnam(config["user"])
    if account.pw_uid < 1000:
        raise UpdateError("Boot updates need the regular kiosk login account.")
    checkout = Path(config["checkout"])
    if not checkout.is_absolute() or not checkout.is_dir() or "\n" in str(checkout):
        raise UpdateError("The configured Git checkout is missing or invalid.")
    return config


class Updater:
    def __init__(self, config, base=BASE):
        self.config = config
        self.base = Path(base)
        self.checkout = Path(config["checkout"])
        self.account = pwd.getpwnam(config["user"])
        self.deadline = time.monotonic() + 140

    def run(self, command, *, timeout=15, label="Command", user=False, output=None, **kwargs):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise UpdateError("Boot update time limit reached.")
        environment = {
            "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
            "HOME": self.account.pw_dir if user else "/root",
            "USER": self.config["user"] if user else "root",
            "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never",
            "GIT_ASKPASS": "/bin/false", "SSH_ASKPASS": "/bin/false",
            "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=yes",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if user:
            command = ["runuser", "-u", self.config["user"], "--", "env",
                       *[f"{key}={value}" for key, value in environment.items()], *map(str, command)]
        with subprocess.Popen(command, cwd="/", env=environment, stdout=output or subprocess.PIPE,
                              stderr=subprocess.PIPE, start_new_session=True, **kwargs) as process:
            try:
                output, _ = process.communicate(timeout=min(timeout, remaining))
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise UpdateError(f"{label} timed out.") from None
            if process.returncode:
                # Git errors can contain credential-bearing remote URLs. Never log them.
                raise UpdateError(f"{label} failed (exit {process.returncode}).")
            return output or b""

    def git(self, *arguments, timeout=15, output=None):
        return self.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
             "-c", "gc.auto=0", "-c", "maintenance.auto=false",
             "-C", str(self.checkout), *arguments],
            user=True, timeout=timeout, label="Git " + arguments[0], output=output,
        )

    def installed(self):
        current = self.base / "current"
        if not current.is_symlink():
            raise UpdateError("Install the kiosk before enabling boot updates.")
        release = current.resolve(strict=True)
        if release.parent != self.base / "releases":
            raise UpdateError("The installed release is outside the release directory.")
        root_managed(release)
        marker = release / ".source-commit"
        root_managed(marker)
        commit = marker.read_text().strip()
        if not re.fullmatch(r"[0-9a-f]{40}", commit) or release.name != commit:
            raise UpdateError("The installed release marker is invalid.")
        runtime = (release / ".venv").resolve(strict=True)
        if not runtime.is_relative_to(self.base / "releases"):
            raise UpdateError("The installed Python runtime is outside the release directory.")
        root_managed(runtime)
        if not (runtime / "bin/python").exists():
            raise UpdateError("The installed Python runtime is missing.")
        return release, commit, runtime

    def check_checkout(self):
        branch = self.config["branch"]
        self.git("check-ref-format", "--branch", branch)
        actual = self.git("symbolic-ref", "--short", "HEAD").decode().strip()
        if actual != branch:
            raise UpdateError("The checkout is on another branch. No branch was changed.")
        if self.git("status", "--porcelain", "--untracked-files=no").strip():
            raise UpdateError("The checkout has local tracked edits. Preserve them before updating.")
        origin = self.git("remote", "get-url", "origin").decode().strip()
        if not re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)[\w.-]+/[\w.-]+(?:\.git)?", origin):
            raise UpdateError("Origin must be a GitHub HTTPS or SSH URL without an embedded token.")

    def check_idle(self):
        for service in SERVICES:
            state = self.run(["systemctl", "show", "--property=ActiveState", "--value", service + ".service"],
                             label="Service state check").decode().strip()
            if state not in {"inactive", "failed"}:
                raise UpdateError("A kiosk or display service is running. Boot updates only run before kiosk startup.")

    def fetch(self):
        end = min(self.deadline, time.monotonic() + 50)
        while True:
            try:
                self.git("fetch", "--no-tags", "origin", "refs/heads/" + self.config["branch"],
                         timeout=max(0.1, min(20, end - time.monotonic())))
                break
            except UpdateError:
                if time.monotonic() + 5 >= end:
                    raise UpdateError("GitHub was unavailable or authentication failed. Try again at the next boot.") from None
                time.sleep(5)
        target = self.git("rev-parse", "--verify", "FETCH_HEAD^{commit}").decode().strip()
        if not re.fullmatch(r"[0-9a-f]{40}", target):
            raise UpdateError("GitHub returned an invalid commit.")
        return target

    def check_contract(self, installed_commit, target):
        changed = self.git("diff", "--name-only", installed_commit, target, "--", *CONTRACT).strip()
        if changed:
            raise UpdateError("Dependencies or Pi setup files changed. Run git pull --ff-only and bash scripts/install-pi.sh, then enable boot updates again.")

    def extract(self, archive, destination):
        with tarfile.open(fileobj=io.BytesIO(archive) if isinstance(archive, bytes) else archive) as bundle:
            members = bundle.getmembers()
            for member in members:
                path = PurePosixPath(member.name)
                if (path.is_absolute() or ".." in path.parts or not path.parts
                        or path.parts[0] in {".venv", ".source-commit", ".git"}
                        or not (member.isfile() or member.isdir())):
                    raise UpdateError("The new release has an unsafe archive entry.")
            for member in members:
                path = destination / member.name
                if member.isdir():
                    path.mkdir(parents=True, exist_ok=True, mode=0o755)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                    with bundle.extractfile(member) as source, path.open("xb") as output:
                        shutil.copyfileobj(source, output)
                        output.flush()
                        os.fsync(output.fileno())
                    path.chmod(0o755 if member.mode & 0o111 else 0o644)

    def validate_release(self, release):
        python = release / ".venv/bin/python"
        self.run([str(python), "-I", str(release / "scripts/check-pi-runtime.py")],
                 user=True, timeout=20, label="Dependency check")
        self.run([str(python), "-I", "-c", SMOKE, str(release)],
                 user=True, timeout=25, label="Isolated kiosk check")

    def publish(self, release):
        link = self.base / f".boot-current-{os.getpid()}"
        try:
            link.symlink_to(release)
            os.replace(link, self.base / "current")
            descriptor = os.open(self.base, os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            link.unlink(missing_ok=True)

    def update(self):
        old_release, old_commit, runtime = self.installed()
        self.check_checkout()
        self.check_idle()
        target = self.fetch()
        if target == old_commit:
            note(f"Kiosk is current ({target[:12]}).")
            return False
        try:
            self.git("merge-base", "--is-ancestor", old_commit, target)
            self.git("merge-base", "--is-ancestor", "HEAD", target)
        except UpdateError:
            raise UpdateError("Git history diverged or was rewritten. The installed version and local commits were retained.") from None
        self.check_contract(old_commit, target)
        # Git protects untracked collisions. Disable hooks and never reset/clean.
        self.git("-c", "merge.autoStash=false", "merge", "--ff-only", "--no-edit", "--no-overwrite-ignore", target)
        self.check_checkout()
        if self.git("rev-parse", "HEAD").decode().strip() != target:
            raise UpdateError("The checkout changed during the update.")
        release = self.base / "releases" / target
        if release.exists() or release.is_symlink():
            # A previous interrupted run can leave an unused release; validate it again.
            root_managed(release)
            if (release / ".source-commit").read_text().strip() != target:
                raise UpdateError("An existing release has an invalid source marker.")
            if (release / ".venv").resolve() != runtime:
                raise UpdateError("An existing release uses another runtime. Install it manually.")
        else:
            if shutil.disk_usage(self.base).free < 256 * 1024 * 1024:
                raise UpdateError("Less than 256 MiB of free space remains. Free space before updating.")
            stage = Path(tempfile.mkdtemp(prefix=".boot-", dir=self.base / "releases"))
            try:
                stage.chmod(0o755)
                # Screenshots make this archive large; spool it to disk on a 512 MB Pi.
                with tempfile.TemporaryFile(dir=self.base / "releases") as archive:
                    self.git("archive", "--format=tar", target, timeout=30, output=archive)
                    archive.seek(0)
                    self.extract(archive, stage)
                (stage / ".venv").symlink_to(runtime)
                with (stage / ".source-commit").open("w") as marker:
                    marker.write(target + "\n")
                    marker.flush()
                    os.fsync(marker.fileno())
                # Make files and directory entries durable before publishing the link.
                for directory, _, _ in os.walk(stage, topdown=False, followlinks=False):
                    descriptor = os.open(directory, os.O_DIRECTORY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                stage.rename(release)
                descriptor = os.open(release.parent, os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        # Check at its permanent path, including after a crash left a release behind.
        self.validate_release(release)
        self.check_idle()
        if (self.base / "current").resolve() != old_release:
            raise UpdateError("Another installer changed the active release.")
        self.publish(release)
        note(f"Installed GitHub commit {target[:12]}. Records and settings were retained.")
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check configuration without network access or changes")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run via the boot service, or use sudo with --check.")
    os.umask(0o022)
    try:
        updater = Updater(load_config())
        if args.check:
            _, installed_commit, _ = updater.installed()
            updater.check_checkout()
            updater.check_contract(installed_commit, updater.git("rev-parse", "HEAD").decode().strip())
            note("Boot update configuration is valid. Network access is checked at boot.")
            return 0
        descriptor = os.open(LOCK, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(descriptor, "a") as lock:
            details = os.fstat(lock.fileno())
            if details.st_uid != 0 or not stat.S_ISREG(details.st_mode) or details.st_mode & 0o022:
                raise UpdateError("The deployment lock is not a protected root-owned file.")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise UpdateError("Another kiosk installer or updater is running.") from None
            updater.update()
    except (UpdateError, OSError, ValueError, KeyError, tarfile.TarError) as error:
        note(f"Update skipped: {error}")
        note("Starting the installed kiosk version. See docs/RASPBERRY_PI_SETUP.md for recovery.")
        return 1 if args.check else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
