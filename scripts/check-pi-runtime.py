#!/usr/bin/env python3
"""Validate the kiosk's dependencies without checking unrelated OS applications."""
from importlib import import_module, metadata
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


def check_requirements(requirements):
    pending = [(Requirement(value), "") for value in requirements]
    visited = set()
    problems = []
    environment = default_environment()
    while pending:
        requirement, extra = pending.pop()
        if requirement.marker and not requirement.marker.evaluate({**environment, "extra": extra}):
            continue
        name = canonicalize_name(requirement.name)
        try:
            installed = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            problems.append(f"Missing package: {requirement}")
            continue
        if requirement.specifier and not requirement.specifier.contains(installed.version, prereleases=True):
            problems.append(f"{requirement} is required; installed version is {installed.version}")
        for selected_extra in {"", *requirement.extras}:
            key = (name, selected_extra)
            if key in visited:
                continue
            visited.add(key)
            pending.extend((Requirement(value), selected_extra) for value in installed.requires or ())
    return sorted(set(problems))


def main():
    source = Path(__file__).resolve().parents[1] / "requirements.txt"
    requirements = [line.strip() for line in source.read_text().splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
    problems = check_requirements([*requirements, "gunicorn", "lgpio"])
    if problems:
        raise SystemExit("Kiosk runtime dependencies are incomplete:\n" + "\n".join(problems))
    for module in ("flask", "google.auth", "google_auth_oauthlib", "requests", "gpiozero", "lgpio", "gunicorn"):
        import_module(module)
    print("Kiosk runtime dependencies and the GPIO driver import successfully.")


if __name__ == "__main__":
    main()
