"""Project, interpreter, Django version and dependency checks."""

from __future__ import annotations

import re
import sys
from collections.abc import Iterator
from datetime import date
from pathlib import Path

from django_doctor.branding import CLI_NAME
from django_doctor.diagnostics.context import DoctorContext
from django_doctor.diagnostics.models import CheckResult, Status

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

#: Django release -> end of extended support (from djangoproject.com/download).
DJANGO_SUPPORT_ENDS = {
    "3.2": date(2024, 4, 1), "4.0": date(2023, 4, 1), "4.1": date(2023, 12, 1),
    "4.2": date(2026, 4, 30), "5.0": date(2025, 4, 30), "5.1": date(2025, 12, 31),
    "5.2": date(2028, 4, 30), "6.0": date(2027, 4, 30), "6.1": date(2027, 12, 31),
}

_REQ_NAME = re.compile(r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)")


def check_project(ctx: DoctorContext) -> Iterator[CheckResult]:
    p = ctx.project
    details = [f"Root: {p.root}", f"manage.py: {p.manage_py.relative_to(p.root)}"]
    if p.settings:
        details.append(f"Settings: {p.settings.module} (from {p.settings.origin})")
    details.append(f"Python interpreter: {p.python}")
    yield CheckResult("project.detected", "Project", Status.OK, "Django project detected", details)
    if not p.settings:
        yield CheckResult("project.settings_module", "Project", Status.ERROR, "Settings module could not be determined",
                          ["manage.py does not set DJANGO_SETTINGS_MODULE and no settings.py was found."],
                          hint="Pass --settings myproject.settings or set it in [tool.djdoctor].")
    for note in p.notes:
        yield CheckResult("project.note", "Project", Status.WARNING, note)


def _parse_requirements(path: Path) -> list[str]:
    names: list[str] = []
    try:
        if path.name == "pyproject.toml":
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            deps = list((data.get("project") or {}).get("dependencies") or [])
            poetry = ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
            deps += [k for k in poetry if k.lower() != "python"]
        elif path.name == "Pipfile":
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            deps = list((data.get("packages") or {}).keys())
        elif path.name == "setup.cfg":
            return []
        else:
            deps = []
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.split("#", 1)[0].strip()
                if not line or line.startswith(("-", "git+", "http:", "https:", "file:")):
                    continue
                deps.append(line)
    except (OSError, tomllib.TOMLDecodeError):
        return []
    for dep in deps:
        dep = dep.split(";", 1)[0]
        m = _REQ_NAME.match(dep)
        if m:
            names.append(m.group("name"))
    return names


def check_dependencies(ctx: DoctorContext) -> Iterator[CheckResult]:
    files = [f for f in ctx.project.dependency_files() if f.name != "setup.cfg"]
    wanted: dict[str, Path] = {}
    for f in files:
        for name in _parse_requirements(f):
            # Skip the project's own package in pyproject.toml.
            wanted.setdefault(name, f)
    probe = ctx.probe("packages", {"distributions": sorted(set(wanted) | {"Django"})})
    versions = probe.data.get("distributions", {}) if probe.ok else {}

    django_version = versions.get("Django")
    if not django_version:
        yield CheckResult("dependencies.django", "Dependencies", Status.ERROR, "Django is not installed in this Python environment",
                          [f"Interpreter: {ctx.project.python}"],
                          hint="Activate your project's virtualenv or pass --python /path/to/venv/bin/python.")
        return
    info = ctx.info or {}
    py = (info.get("python") or {}).get("version")
    yield CheckResult("dependencies.versions", "Dependencies", Status.OK,
                      f"Django {django_version}" + (f" on Python {py}" if py else ""))

    series = ".".join(django_version.split(".")[:2])
    ends = DJANGO_SUPPORT_ENDS.get(series)
    if ends and ends < date.today():
        yield CheckResult("dependencies.django_eol", "Dependencies", Status.WARNING,
                          f"Django {series} no longer receives security updates (support ended {ends.isoformat()})",
                          hint="Plan an upgrade to a supported release (see djangoproject.com/download).")

    if not files:
        yield CheckResult("dependencies.files", "Dependencies", Status.INFO, "No requirements.txt / pyproject.toml found",
                          hint="Pin your dependencies so the project can be reproduced.")
        return
    missing = sorted(n for n in wanted if not versions.get(n) and n.lower() not in _own_names(ctx))
    if missing:
        details = [f"{n} (listed in {wanted[n].name})" for n in missing[:15]]
        yield CheckResult("dependencies.missing", "Dependencies", Status.WARNING,
                          f"{len(missing)} listed package(s) are not installed", details,
                          hint="Install them into the project's environment, e.g. pip install -r requirements.txt")
    else:
        yield CheckResult("dependencies.installed", "Dependencies", Status.OK,
                          f"All {len(wanted)} listed dependencies are installed")


def _own_names(ctx: DoctorContext) -> set[str]:
    """The project's own distribution name(s) from pyproject.toml, not a dependency."""
    names = set()
    for f in ctx.project.dependency_files():
        if f.name == "pyproject.toml":
            try:
                data = tomllib.loads(f.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError):
                continue
            name = (data.get("project") or {}).get("name")
            if name:
                names.add(name.lower())
    return names


def not_loaded_hint() -> str:
    return f"Fix the settings error above, then run `{CLI_NAME} doctor` again."
